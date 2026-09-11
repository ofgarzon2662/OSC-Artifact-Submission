import http.server
import json
import logging
import os
import ssl
import threading
import time

import jsonschema
import pika
import requests
from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

ENVIRONMENT = os.getenv("ENVIRONMENT", "local").lower()
IS_DOCKER = os.path.exists("/.dockerenv") or ENVIRONMENT == "docker"
RABBITMQ_HOST = os.getenv("RABBITMQ_HOST", "rabbitmq" if IS_DOCKER else "localhost")
RABBITMQ_PORT = int(os.getenv("RABBITMQ_PORT", "5672"))
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "user")
RABBITMQ_PASS = os.getenv("RABBITMQ_PASS", "password")
RABBITMQ_TLS = os.getenv(
    "RABBITMQ_TLS", "true" if RABBITMQ_PORT == 5671 else "false"
).lower() == "true"
RABBITMQ_CA_PATH = os.getenv("RABBITMQ_CA_PATH", "")
RABBITMQ_QUEUE_TYPE = os.getenv("RABBITMQ_QUEUE_TYPE", "classic")
MAX_DELIVERY_ATTEMPTS = int(os.getenv("MAX_DELIVERY_ATTEMPTS", "4"))
RETRY_DELAY_MS = int(os.getenv("RETRY_DELAY_MS", "5000"))

RABBITMQ_QUEUE_SUBMITTED = os.getenv(
    "RABBITMQ_QUEUE_SUBMITTED", "artifact.submitted.queue"
)
RABBITMQ_QUEUE_UPDATED = os.getenv(
    "RABBITMQ_QUEUE_UPDATED", "artifact.updated.queue"
)
RABBITMQ_QUEUE_WORKFLOW_SUBMITTED = os.getenv(
    "RABBITMQ_QUEUE_WORKFLOW_SUBMITTED", "workflow.submitted.queue"
)
RABBITMQ_QUEUE_WORKFLOW_UPDATED = os.getenv(
    "RABBITMQ_QUEUE_WORKFLOW_UPDATED", "workflow.updated.queue"
)

API_GATEWAY_BASE_URL = os.getenv(
    "API_GATEWAY_BASE_URL",
    "http://osc-api-gateway:3000/api/v1"
    if IS_DOCKER
    else "http://localhost:3000/api/v1",
).rstrip("/")
API_GATEWAY_URL = os.getenv(
    "API_GATEWAY_URL", f"{API_GATEWAY_BASE_URL}/artifacts"
)
API_GATEWAY_WORKFLOW_URL = os.getenv(
    "API_GATEWAY_WORKFLOW_URL", f"{API_GATEWAY_BASE_URL}/workflows"
)
SUBMISSION_LISTENER_API_KEY = os.getenv("SUBMISSION_LISTENER_API_KEY", "")
SUBMISSION_LISTENER_SERVICE_ROLE = os.getenv(
    "SUBMISSION_LISTENER_SERVICE_ROLE", "submitter_listener"
)

COMPLETION_ROUTES = {
    "artifact.submitted": RABBITMQ_QUEUE_SUBMITTED,
    "artifact.updated": RABBITMQ_QUEUE_UPDATED,
    "workflow.submitted": RABBITMQ_QUEUE_WORKFLOW_SUBMITTED,
    "workflow.updated": RABBITMQ_QUEUE_WORKFLOW_UPDATED,
}


class RetryableStatusUpdateError(Exception):
    pass


class PermanentStatusUpdateError(Exception):
    pass


def _schema_path(name):
    if IS_DOCKER and os.path.exists(f"/app/schema/{name}"):
        return f"/app/schema/{name}"
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts", name)


def load_schema(name="artifact.submitted.v1.schema.json"):
    with open(_schema_path(name), "r", encoding="utf-8") as source:
        return json.load(source)


artifact_submitted_schema = load_schema()
artifact_updated_schema = load_schema("artifact.updated.v1.schema.json")


def validate_message(message, schema):
    try:
        jsonschema.validate(instance=message, schema=schema)
        return True
    except jsonschema.exceptions.ValidationError:
        return False


def _validate_workflow_message(message, event_kind):
    timestamp_field = "submittedAt" if event_kind == "submitted" else "updatedAt"
    if not isinstance(message, dict):
        return False
    if not all(field in message for field in ("workflowId", "submissionState", timestamp_field, "version")):
        return False
    if message["version"] != "v1" or message["submissionState"] not in {
        "SUCCESS",
        "FAILED",
    }:
        return False
    if message["submissionState"] == "FAILED" and not message.get("error"):
        return False
    return True


def _status_patch(submission_data):
    patch = {"submissionState": submission_data["submissionState"]}
    for field in ("blockchainTxId", "peerId", "updatedAt"):
        if field in submission_data:
            patch[field] = submission_data[field]
    if submission_data["submissionState"] == "FAILED":
        patch["submissionError"] = submission_data.get("error", "Unknown failure")
    return patch


def _patch_status(url, submission_data, correlation_id=None):
    headers = {
        "Content-Type": "application/json",
        "X-API-Key": SUBMISSION_LISTENER_API_KEY,
        "X-Service-Role": SUBMISSION_LISTENER_SERVICE_ROLE,
        "User-Agent": "osc-submission-listener/3.0",
    }
    if correlation_id:
        headers["X-Correlation-ID"] = correlation_id
    try:
        response = requests.patch(
            url,
            json=_status_patch(submission_data),
            headers=headers,
            timeout=10,
        )
    except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as error:
        raise RetryableStatusUpdateError(type(error).__name__) from error
    except requests.exceptions.RequestException as error:
        raise RetryableStatusUpdateError("API Gateway request failed") from error
    if response.status_code in (408, 425, 429) or response.status_code >= 500:
        raise RetryableStatusUpdateError(
            f"API Gateway returned HTTP {response.status_code}"
        )
    if response.status_code >= 400:
        raise PermanentStatusUpdateError(
            f"API Gateway rejected status update with HTTP {response.status_code}"
        )
    return True


def update_artifact_status(artifact_id, submission_data, correlation_id=None):
    return _patch_status(
        f"{API_GATEWAY_URL}/{artifact_id}", submission_data, correlation_id
    )


def update_workflow_status(workflow_id, submission_data, correlation_id=None):
    return _patch_status(
        f"{API_GATEWAY_WORKFLOW_URL}/{workflow_id}",
        submission_data,
        correlation_id,
    )


def _route(routing_key):
    for topic, queue_name in COMPLETION_ROUTES.items():
        if routing_key in (topic, queue_name):
            return topic
    return "artifact.submitted"


def _rejected_count(properties):
    headers = getattr(properties, "headers", None) or {}
    deaths = headers.get("x-death", []) if isinstance(headers, dict) else []
    return sum(
        int(entry.get("count", 0))
        for entry in deaths
        if isinstance(entry, dict) and entry.get("reason") == "rejected"
    )


def _park_message(channel, topic, body, reason, correlation_id=None):
    accepted = channel.basic_publish(
        exchange="osc.failed.exchange",
        routing_key="status.failed",
        body=body,
        mandatory=True,
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            correlation_id=correlation_id,
            headers={
                "failure-reason": str(reason)[:256],
                "original-routing-key": topic,
            },
        ),
    )
    if accepted is False:
        raise RetryableStatusUpdateError("Failed-message publisher confirm missing")


def _retry_or_park(channel, method, properties, topic, body, reason, correlation_id):
    attempts = _rejected_count(properties) + 1
    if attempts < MAX_DELIVERY_ATTEMPTS:
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    _park_message(channel, topic, body, reason, correlation_id)
    channel.basic_ack(delivery_tag=method.delivery_tag)


def callback(ch, method, properties, body):
    routing_key = getattr(method, "routing_key", "") or ""
    topic = _route(routing_key)
    correlation_id = getattr(properties, "correlation_id", None)
    try:
        message = json.loads(body)
        if topic.startswith("workflow."):
            event_kind = "submitted" if topic.endswith(".submitted") else "updated"
            if not _validate_workflow_message(message, event_kind):
                raise PermanentStatusUpdateError("Invalid workflow completion event")
            update_workflow_status(message["workflowId"], message, correlation_id)
        else:
            schema = (
                artifact_updated_schema
                if topic == "artifact.updated"
                else artifact_submitted_schema
            )
            if not validate_message(message, schema):
                raise PermanentStatusUpdateError("Invalid artifact completion event")
            update_artifact_status(message["artifactId"], message, correlation_id)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except PermanentStatusUpdateError as error:
        _park_message(ch, topic, body, error, correlation_id)
        ch.basic_ack(delivery_tag=method.delivery_tag)
    except (RetryableStatusUpdateError, json.JSONDecodeError, KeyError) as error:
        _retry_or_park(
            ch, method, properties, topic, body, error, correlation_id
        )
    except Exception as error:
        _retry_or_park(
            ch, method, properties, topic, body, error, correlation_id
        )


def _tls_options():
    if ENVIRONMENT in {"aws", "staging", "production"} and not RABBITMQ_TLS:
        raise RuntimeError("RabbitMQ TLS is required outside local development")
    if not RABBITMQ_TLS:
        return None
    context = ssl.create_default_context(cafile=RABBITMQ_CA_PATH or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return pika.SSLOptions(context, RABBITMQ_HOST)


def _queue_arguments(dead_letter_exchange, dead_letter_routing_key):
    arguments = {
        "x-dead-letter-exchange": dead_letter_exchange,
        "x-dead-letter-routing-key": dead_letter_routing_key,
    }
    if RABBITMQ_QUEUE_TYPE == "quorum":
        arguments["x-queue-type"] = "quorum"
    return arguments


def declare_messaging_topology(channel):
    for exchange in (
        "artifact.exchange",
        "osc.listener.retry.exchange",
        "osc.failed.exchange",
    ):
        channel.exchange_declare(
            exchange=exchange, exchange_type="topic", durable=True
        )
    channel.queue_declare(queue="osc.status.failed.queue", durable=True)
    channel.queue_bind(
        exchange="osc.failed.exchange",
        queue="osc.status.failed.queue",
        routing_key="status.failed",
    )
    for topic, queue_name in COMPLETION_ROUTES.items():
        retry_routing_key = f"{topic}.retry"
        retry_queue = f"{queue_name}.retry"
        channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments=_queue_arguments(
                "osc.listener.retry.exchange", retry_routing_key
            ),
        )
        channel.queue_bind(
            exchange="artifact.exchange", queue=queue_name, routing_key=topic
        )
        retry_arguments = _queue_arguments("artifact.exchange", topic)
        retry_arguments["x-message-ttl"] = RETRY_DELAY_MS
        channel.queue_declare(
            queue=retry_queue, durable=True, arguments=retry_arguments
        )
        channel.queue_bind(
            exchange="osc.listener.retry.exchange",
            queue=retry_queue,
            routing_key=retry_routing_key,
        )


def _validate_runtime_configuration():
    if ENVIRONMENT in {"aws", "staging", "production", "docker"}:
        if len(SUBMISSION_LISTENER_API_KEY) < 24:
            raise RuntimeError("SUBMISSION_LISTENER_API_KEY must be configured")


def start_rabbitmq_consumer():
    _validate_runtime_configuration()
    connection = None
    retry_count = 0
    while connection is None and retry_count < 30:
        try:
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS),
                ssl_options=_tls_options(),
                heartbeat=60,
                blocked_connection_timeout=60,
                connection_attempts=3,
                retry_delay=2,
            )
            connection = pika.BlockingConnection(parameters)
        except pika.exceptions.AMQPConnectionError:
            retry_count += 1
            logger.warning("RabbitMQ connection attempt %s/30 failed", retry_count)
            time.sleep(5)
    if connection is None:
        logger.error("RabbitMQ connection attempts exhausted")
        return

    channel = connection.channel()
    declare_messaging_topology(channel)
    channel.confirm_delivery()
    channel.basic_qos(prefetch_count=1)
    for queue_name in COMPLETION_ROUTES.values():
        channel.basic_consume(queue=queue_name, on_message_callback=callback)
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        if not connection.is_closed:
            connection.close()


class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(
            json.dumps(
                {
                    "status": "healthy",
                    "service": "submission-listener",
                    "environment": ENVIRONMENT,
                    "rabbitmqTls": RABBITMQ_TLS,
                }
            ).encode()
        )

    def log_message(self, _format, *args):
        pass


def start_health_server():
    try:
        server = http.server.HTTPServer(("0.0.0.0", 8000), HealthCheckHandler)
        server.serve_forever()
    except Exception as error:
        logger.error("Health server failed: %s", error)


if __name__ == "__main__":
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    start_rabbitmq_consumer()
