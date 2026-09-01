import http.server
import json
import logging
import os
import ssl
import threading
import time
from datetime import datetime, timezone

import pika
from dotenv import load_dotenv

from peer_client import PeerClient

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

RABBITMQ_QUEUE_SUBMIT = os.getenv("RABBITMQ_QUEUE_SUBMIT", "artifact.submit.queue")
RABBITMQ_QUEUE_SUBMITTED = os.getenv(
    "RABBITMQ_QUEUE_SUBMITTED", "artifact.submitted.queue"
)
RABBITMQ_QUEUE_UPDATE = os.getenv("RABBITMQ_QUEUE_UPDATE", "artifact.update.queue")
RABBITMQ_QUEUE_UPDATED = os.getenv(
    "RABBITMQ_QUEUE_UPDATED", "artifact.updated.queue"
)
RABBITMQ_QUEUE_WORKFLOW_SUBMIT = os.getenv(
    "RABBITMQ_QUEUE_WORKFLOW_SUBMIT", "workflow.submit.queue"
)
RABBITMQ_QUEUE_WORKFLOW_SUBMITTED = os.getenv(
    "RABBITMQ_QUEUE_WORKFLOW_SUBMITTED", "workflow.submitted.queue"
)
RABBITMQ_QUEUE_WORKFLOW_UPDATE = os.getenv(
    "RABBITMQ_QUEUE_WORKFLOW_UPDATE", "workflow.update.queue"
)
RABBITMQ_QUEUE_WORKFLOW_UPDATED = os.getenv(
    "RABBITMQ_QUEUE_WORKFLOW_UPDATED", "workflow.updated.queue"
)

FABRIC_BRIDGE_URL = os.getenv(
    "FABRIC_BRIDGE_URL",
    "http://ledger-gateway-nsg:4000" if IS_DOCKER else "http://localhost:4101",
)
NSG_GATEWAY_URL = os.getenv("LEDGER_GATEWAY_NSG_URL", FABRIC_BRIDGE_URL)
CITIZEN_SCIENCE_GATEWAY_URL = os.getenv(
    "LEDGER_GATEWAY_CITIZEN_SCIENCE_URL",
    "http://ledger-gateway-citizen-science:4000"
    if IS_DOCKER
    else "http://localhost:4102",
)
COMMON_GATEWAY_TOKEN = os.getenv("LEDGER_GATEWAY_TOKEN", "")
NSG_GATEWAY_TOKEN = os.getenv("LEDGER_GATEWAY_NSG_TOKEN", COMMON_GATEWAY_TOKEN)
CITIZEN_SCIENCE_GATEWAY_TOKEN = os.getenv(
    "LEDGER_GATEWAY_CITIZEN_SCIENCE_TOKEN", COMMON_GATEWAY_TOKEN
)

peer_client = PeerClient(NSG_GATEWAY_URL, NSG_GATEWAY_TOKEN)
peer_clients = {
    "NSGMSP": peer_client,
    "CitizenScienceMSP": PeerClient(
        CITIZEN_SCIENCE_GATEWAY_URL, CITIZEN_SCIENCE_GATEWAY_TOKEN
    ),
}
SUPPORTED_ORGANIZATIONS = {
    "NSGMSP": "nsg",
    "CitizenScienceMSP": "citizen-science",
}

COMMAND_ROUTES = {
    "artifact.submit": ("artifact.create", RABBITMQ_QUEUE_SUBMIT),
    "artifact.update": ("artifact.update", RABBITMQ_QUEUE_UPDATE),
    "workflow.submit": ("workflow.create", RABBITMQ_QUEUE_WORKFLOW_SUBMIT),
    "workflow.update": ("workflow.update", RABBITMQ_QUEUE_WORKFLOW_UPDATE),
}


class RetryableProcessingError(Exception):
    pass


class PermanentProcessingError(Exception):
    pass


def _completion_message(asset_type, asset_id, result, event_kind, correlation_id=None):
    timestamp_field = "submittedAt" if event_kind == "submitted" else "updatedAt"
    message = {
        f"{asset_type}Id": asset_id,
        "submissionState": "SUCCESS" if result.get("success") else "FAILED",
        timestamp_field: datetime.now(timezone.utc).isoformat(),
        "version": "v1",
    }
    tx_id = result.get("txId") or result.get("transactionId") or result.get("txID")
    if result.get("success") and tx_id:
        message["blockchainTxId"] = str(tx_id)[:128]
    if result.get("peerId"):
        message["peerId"] = str(result["peerId"])[:256]
    if not result.get("success"):
        message["error"] = str(result.get("error") or "Ledger operation failed")[:512]
    return message, correlation_id


def _publish_completion(channel, routing_key, message, correlation_id=None):
    accepted = channel.basic_publish(
        exchange="artifact.exchange",
        routing_key=routing_key,
        body=json.dumps(message),
        mandatory=True,
        properties=pika.BasicProperties(
            delivery_mode=2,
            content_type="application/json",
            message_id=correlation_id,
            correlation_id=correlation_id,
        ),
    )
    if accepted is False:
        raise RetryableProcessingError("RabbitMQ publisher confirm was not received")


def publish_artifact_submitted(
    channel, artifact_id, submission_result, correlation_id=None
):
    message, correlation_id = _completion_message(
        "artifact", artifact_id, submission_result, "submitted", correlation_id
    )
    _publish_completion(channel, "artifact.submitted", message, correlation_id)


def publish_artifact_updated(channel, artifact_id, update_result, correlation_id=None):
    message, correlation_id = _completion_message(
        "artifact", artifact_id, update_result, "updated", correlation_id
    )
    _publish_completion(channel, "artifact.updated", message, correlation_id)


def publish_workflow_submitted(
    channel, workflow_id, submission_result, correlation_id=None
):
    message, correlation_id = _completion_message(
        "workflow", workflow_id, submission_result, "submitted", correlation_id
    )
    _publish_completion(channel, "workflow.submitted", message, correlation_id)


def publish_workflow_updated(channel, workflow_id, update_result, correlation_id=None):
    message, correlation_id = _completion_message(
        "workflow", workflow_id, update_result, "updated", correlation_id
    )
    _publish_completion(channel, "workflow.updated", message, correlation_id)


def _validate_envelope(command, operation):
    if not isinstance(command, dict):
        raise PermanentProcessingError("Command must be a JSON object")
    if command.get("contractVersion") != "v3":
        raise PermanentProcessingError("Only contractVersion v3 is accepted")
    organization = command.get("organization")
    request = command.get("request")
    if not isinstance(organization, dict) or not isinstance(request, dict):
        raise PermanentProcessingError("Organization and request metadata are required")
    msp_id = organization.get("mspId")
    organization_id = organization.get("id")
    if SUPPORTED_ORGANIZATIONS.get(msp_id) != organization_id:
        raise PermanentProcessingError("Organization and MSP routing do not match")
    if request.get("organizationId") != organization_id:
        raise PermanentProcessingError("Request organization does not match routing")
    if request.get("operation") != operation:
        raise PermanentProcessingError("Request operation does not match routing")
    correlation_id = command.get("correlationId")
    if not correlation_id or request.get("correlationId") != correlation_id:
        raise PermanentProcessingError("Correlation IDs are missing or inconsistent")
    if not request.get("authenticatedUserId") or not request.get("requestedAt"):
        raise PermanentProcessingError("Authenticated request metadata is incomplete")


def _gateway_client(command):
    organization = command.get("organization") if isinstance(command, dict) else None
    if not organization:
        return peer_client
    client = peer_clients.get(organization.get("mspId"))
    if client is None:
        raise PermanentProcessingError("No Ledger Gateway exists for this organization")
    return client


def _clean(command, allowed_fields):
    return {
        key: value
        for key, value in command.items()
        if key in allowed_fields and value is not None
    }


def _handle_result(result, publisher, channel, asset_id, correlation_id):
    if result.get("success"):
        publisher(channel, asset_id, result, correlation_id)
        return True
    if result.get("retryable"):
        raise RetryableProcessingError(result.get("error") or "Ledger Gateway unavailable")
    publisher(channel, asset_id, result, correlation_id)
    return True


def process_artifact_submission(channel, artifact_id, artifact_data):
    if artifact_data.get("manifest") is None:
        publish_artifact_submitted(
            channel,
            artifact_id,
            {"success": False, "error": "Missing manifest"},
            artifact_data.get("correlationId"),
        )
        return True
    footprint = artifact_data.get("footprint")
    if not (
        isinstance(footprint, str)
        and len(footprint) == 64
        and all(character in "0123456789abcdefABCDEF" for character in footprint)
    ):
        publish_artifact_submitted(
            channel,
            artifact_id,
            {"success": False, "error": "Missing or invalid footprint"},
            artifact_data.get("correlationId"),
        )
        return True
    command = _clean(
        artifact_data,
        {
            "contractVersion",
            "artifactId",
            "organization",
            "manifest",
            "title",
            "visibility",
            "footprint",
            "description",
            "submission_comment",
            "keywords",
            "links",
            "dois",
            "fundingAgencies",
            "acknowledgements",
            "contributor",
            "correlationId",
            "request",
        },
    )
    if isinstance(command.get("dois"), list):
        command["dois"] = [
            doi for doi in command["dois"] if isinstance(doi, str) and doi.strip()
        ]
    result = _gateway_client(artifact_data).submit_artifact(command)
    return _handle_result(
        result,
        publish_artifact_submitted,
        channel,
        artifact_id,
        artifact_data.get("correlationId"),
    )


def process_artifact_update(channel, artifact_id, patch_data):
    metadata = patch_data if isinstance(patch_data, dict) else {}
    patch = metadata.get("patch") if isinstance(metadata.get("patch"), dict) else metadata
    routing_fields = {
        "artifactId",
        "contractVersion",
        "organization",
        "request",
        "correlationId",
        "contributor",
    }
    patch = {key: value for key, value in patch.items() if key not in routing_fields}
    footprint = patch.get("footprint")
    if footprint is not None and not (
        isinstance(footprint, str)
        and len(footprint) == 64
        and all(character in "0123456789abcdefABCDEF" for character in footprint)
    ):
        publish_artifact_updated(
            channel,
            artifact_id,
            {"success": False, "error": "Invalid footprint in update"},
            metadata.get("correlationId"),
        )
        return True
    result = _gateway_client(metadata).update_artifact(
        artifact_id,
        patch,
        organization=metadata.get("organization"),
        contract_version=metadata.get("contractVersion", "v3"),
        request=metadata.get("request"),
        correlation_id=metadata.get("correlationId"),
    )
    return _handle_result(
        result,
        publish_artifact_updated,
        channel,
        artifact_id,
        metadata.get("correlationId"),
    )


def process_workflow_submission(channel, workflow_id, workflow_data):
    command = _clean(
        workflow_data,
        {
            "contractVersion",
            "workflowId",
            "organization",
            "title",
            "visibility",
            "description",
            "submission_comment",
            "keywords",
            "githubRepositories",
            "artifactIds",
            "contributor",
            "correlationId",
            "request",
        },
    )
    result = _gateway_client(workflow_data).submit_workflow(command)
    return _handle_result(
        result,
        publish_workflow_submitted,
        channel,
        workflow_id,
        workflow_data.get("correlationId"),
    )


def process_workflow_update(channel, workflow_id, patch_data):
    metadata = patch_data if isinstance(patch_data, dict) else {}
    patch = metadata.get("patch") if isinstance(metadata.get("patch"), dict) else metadata
    routing_fields = {
        "workflowId",
        "contractVersion",
        "organization",
        "request",
        "correlationId",
        "contributor",
    }
    patch = {key: value for key, value in patch.items() if key not in routing_fields}
    result = _gateway_client(metadata).update_workflow(
        workflow_id,
        patch,
        organization=metadata.get("organization"),
        contract_version=metadata.get("contractVersion", "v3"),
        request=metadata.get("request"),
        correlation_id=metadata.get("correlationId"),
    )
    return _handle_result(
        result,
        publish_workflow_updated,
        channel,
        workflow_id,
        metadata.get("correlationId"),
    )


def _route(routing_key):
    for topic, (operation, queue_name) in COMMAND_ROUTES.items():
        if routing_key in (topic, queue_name):
            return topic, operation
    return "artifact.submit", "artifact.create"


def _rejected_count(properties):
    headers = getattr(properties, "headers", None) or {}
    deaths = headers.get("x-death", []) if isinstance(headers, dict) else []
    return sum(
        int(entry.get("count", 0))
        for entry in deaths
        if isinstance(entry, dict) and entry.get("reason") == "rejected"
    )


def _terminal_failure(channel, topic, asset_id, error, correlation_id):
    result = {"success": False, "error": str(error)[:512]}
    if topic == "artifact.submit":
        publish_artifact_submitted(channel, asset_id, result, correlation_id)
    elif topic == "artifact.update":
        publish_artifact_updated(channel, asset_id, result, correlation_id)
    elif topic == "workflow.submit":
        publish_workflow_submitted(channel, asset_id, result, correlation_id)
    else:
        publish_workflow_updated(channel, asset_id, result, correlation_id)


def _retry_or_finish(channel, method, properties, topic, asset_id, message, error):
    attempts = _rejected_count(properties) + 1
    if attempts < MAX_DELIVERY_ATTEMPTS:
        logger.warning(
            "Transient %s failure for %s; scheduling retry %s/%s",
            topic,
            asset_id,
            attempts,
            MAX_DELIVERY_ATTEMPTS,
        )
        channel.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
        return
    _terminal_failure(
        channel, topic, asset_id, error, message.get("correlationId")
    )
    channel.basic_ack(delivery_tag=method.delivery_tag)


def callback(ch, method, properties, body):
    routing_key = getattr(method, "routing_key", "") or ""
    topic, operation = _route(routing_key)
    message = None
    asset_id = None
    try:
        message = json.loads(body)
        id_field = "workflowId" if topic.startswith("workflow.") else "artifactId"
        asset_id = message.get(id_field) if isinstance(message, dict) else None
        _validate_envelope(message, operation)
        if not asset_id:
            raise PermanentProcessingError(f"Missing {id_field}")
        if topic == "artifact.submit":
            handled = process_artifact_submission(ch, asset_id, message)
        elif topic == "artifact.update":
            handled = process_artifact_update(ch, asset_id, message)
        elif topic == "workflow.submit":
            handled = process_workflow_submission(ch, asset_id, message)
        else:
            handled = process_workflow_update(ch, asset_id, message)
        if handled:
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except json.JSONDecodeError:
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except PermanentProcessingError as error:
        if message and asset_id:
            _terminal_failure(
                ch, topic, asset_id, error, message.get("correlationId")
            )
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except RetryableProcessingError as error:
        _retry_or_finish(ch, method, properties, topic, asset_id, message, error)
    except Exception as error:
        if message and asset_id:
            _retry_or_finish(ch, method, properties, topic, asset_id, message, error)
        else:
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)


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
    channel.exchange_declare(
        exchange="artifact.exchange", exchange_type="topic", durable=True
    )
    channel.exchange_declare(
        exchange="osc.retry.exchange", exchange_type="topic", durable=True
    )
    for topic, (_, queue_name) in COMMAND_ROUTES.items():
        retry_routing_key = f"{topic}.retry"
        retry_queue = f"{queue_name}.retry"
        channel.queue_declare(
            queue=queue_name,
            durable=True,
            arguments=_queue_arguments("osc.retry.exchange", retry_routing_key),
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
            exchange="osc.retry.exchange",
            queue=retry_queue,
            routing_key=retry_routing_key,
        )


def _validate_runtime_configuration():
    if ENVIRONMENT in {"aws", "staging", "production", "docker"}:
        if len(NSG_GATEWAY_TOKEN) < 24 or len(CITIZEN_SCIENCE_GATEWAY_TOKEN) < 24:
            raise RuntimeError("Both Ledger Gateway tokens must be configured")


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
    for _, (_, queue_name) in COMMAND_ROUTES.items():
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
                    "service": "submission-worker",
                    "environment": ENVIRONMENT,
                    "organizations": sorted(SUPPORTED_ORGANIZATIONS.values()),
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
