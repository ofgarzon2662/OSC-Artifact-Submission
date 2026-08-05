import json
import os
import ssl
import pika
import requests
import logging
import jsonschema
import time
import http.server
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables from .env file (only if running locally)
if os.path.exists('.env'):
    load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Detect environment
ENVIRONMENT = os.getenv('ENVIRONMENT', 'local')
IS_DOCKER = os.path.exists('/.dockerenv') or ENVIRONMENT == 'docker'

# Environment variables with secure defaults
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'rabbitmq' if IS_DOCKER else 'localhost')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'user')
RABBITMQ_PASS = os.getenv('RABBITMQ_PASS', 'password')
RABBITMQ_TLS = os.getenv(
    'RABBITMQ_TLS', 'true' if RABBITMQ_PORT == 5671 else 'false'
).lower() == 'true'
RABBITMQ_QUEUE_SUBMITTED = os.getenv('RABBITMQ_QUEUE_SUBMITTED', 'artifact.submitted.queue')
RABBITMQ_QUEUE_UPDATED = os.getenv('RABBITMQ_QUEUE_UPDATED', 'artifact.updated.queue')

# Workflow queues
RABBITMQ_QUEUE_WORKFLOW_SUBMITTED = os.getenv('RABBITMQ_QUEUE_WORKFLOW_SUBMITTED', 'workflow.submitted.queue')
RABBITMQ_QUEUE_WORKFLOW_UPDATED = os.getenv('RABBITMQ_QUEUE_WORKFLOW_UPDATED', 'workflow.updated.queue')

# API Gateway URL with environment-aware defaults
if IS_DOCKER:
    API_GATEWAY_URL = os.getenv('API_GATEWAY_URL', 'http://osc-api-gateway:3000/api/v1/artifacts')
    API_GATEWAY_WORKFLOW_URL = os.getenv('API_GATEWAY_WORKFLOW_URL', 'http://osc-api-gateway:3000/api/v1/workflows')
else:
    API_GATEWAY_URL = os.getenv('API_GATEWAY_URL', 'http://localhost:3000/api/v1/artifacts')
    API_GATEWAY_WORKFLOW_URL = os.getenv('API_GATEWAY_WORKFLOW_URL', 'http://localhost:3000/api/v1/workflows')

# IMPORTANT: Never hardcode real API keys in source code
SUBMISSION_LISTENER_API_KEY = os.getenv('SUBMISSION_LISTENER_API_KEY', 'test-api-key')
SUBMISSION_LISTENER_SERVICE_ROLE = os.getenv('SUBMISSION_LISTENER_SERVICE_ROLE', 'submitter_listener')

if SUBMISSION_LISTENER_API_KEY == 'test-api-key' or SUBMISSION_LISTENER_API_KEY == '':
    logger.warning("SUBMISSION_LISTENER_API_KEY is not set. Set SUBMISSION_LISTENER_API_KEY environment variable.")

# Log environment info
logger.info(f"Environment: {'Docker' if IS_DOCKER else 'Local'}")
logger.info(f"RabbitMQ: {RABBITMQ_HOST}:{RABBITMQ_PORT}")
logger.info(f"API Gateway: {API_GATEWAY_URL}")
logger.info(f"SUBMISSION_LISTENER_API_KEY configured: {'***' if SUBMISSION_LISTENER_API_KEY and SUBMISSION_LISTENER_API_KEY != 'test-api-key' else 'NOT SET'}")
logger.info(f"SUBMISSION_LISTENER_SERVICE_ROLE configured: {SUBMISSION_LISTENER_SERVICE_ROLE}")

def load_schema():
    """Load the JSON schema from the appropriate path based on environment"""
    if IS_DOCKER:
        # Docker path
        schema_path = '/app/schema/artifact.submitted.v1.schema.json'
    else:
        # Local development path
        current_dir = os.path.dirname(os.path.abspath(__file__))
        schema_path = os.path.join(current_dir, 'contracts', 'artifact.submitted.v1.schema.json')
    
    if not os.path.exists(schema_path):
        raise FileNotFoundError(f"Schema file not found at: {schema_path}")
    
    logger.info(f"Loading schema from: {schema_path}")
    with open(schema_path, 'r') as f:
        return json.load(f)

# Load the schema
artifact_submitted_schema = load_schema()

# Load the updated schema
if IS_DOCKER:
    updated_schema_path = '/app/schema/artifact.updated.v1.schema.json'
else:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    updated_schema_path = os.path.join(current_dir, 'contracts', 'artifact.updated.v1.schema.json')
if not os.path.exists(updated_schema_path):
    raise FileNotFoundError(f"Schema file not found at: {updated_schema_path}")
with open(updated_schema_path, 'r') as f:
    artifact_updated_schema = json.load(f)

def update_artifact_status(artifact_id, submission_data):
    """
    Send a PATCH request to the API Gateway to update an artifact's status.
    Uses API Key authentication with role-based access control.
    """
    url = f"{API_GATEWAY_URL}/{artifact_id}"
    
    # Prepare data for PATCH request based on the artifact.submitted message
    patch_data = {
        'submissionState': submission_data['submissionState']
    }
    
    # Include blockchainTxId if present
    if 'blockchainTxId' in submission_data:
        patch_data['blockchainTxId'] = submission_data['blockchainTxId']
    
    # Add peerId if present
    if 'peerId' in submission_data:
        patch_data['peerId'] = submission_data['peerId']

    # Include updatedAt only if present (e.g., for update events)
    if 'updatedAt' in submission_data:
        patch_data['updatedAt'] = submission_data['updatedAt']

    # If the submission failed, include the error message
    if submission_data['submissionState'] == 'FAILED' and 'error' in submission_data:
        patch_data['submissionError'] = submission_data['error']
    
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': SUBMISSION_LISTENER_API_KEY,
        'X-Service-Role': SUBMISSION_LISTENER_SERVICE_ROLE,
        'User-Agent': 'submission-listener/1.0'
    }
    
    try:
        logger.info(f"Sending PATCH request to {url} for artifact {artifact_id}")
        logger.debug(f"Request payload: {json.dumps(patch_data, indent=2)}")
        response = requests.patch(url, json=patch_data, headers=headers, timeout=10)
        response.raise_for_status()
        
        logger.info(f"Successfully updated artifact {artifact_id} status to {submission_data['submissionState']}")
        logger.debug(f"API Gateway response: {response.status_code} - {response.text}")
        return True
        
    except requests.exceptions.Timeout:
        logger.error(f"Timeout updating artifact {artifact_id}")
        return False
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error updating artifact {artifact_id}: {e.response.status_code} - {e.response.text}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error updating artifact {artifact_id}: {str(e)}")
        return False

def update_workflow_status(workflow_id, submission_data):
    """
    Send a PATCH request to the API Gateway to update a workflow's status.
    Uses API Key authentication with role-based access control.
    """
    url = f"{API_GATEWAY_WORKFLOW_URL}/{workflow_id}"

    patch_data = {
        'submissionState': submission_data['submissionState']
    }

    if 'blockchainTxId' in submission_data:
        patch_data['blockchainTxId'] = submission_data['blockchainTxId']
    if 'peerId' in submission_data:
        patch_data['peerId'] = submission_data['peerId']
    if 'updatedAt' in submission_data:
        patch_data['updatedAt'] = submission_data['updatedAt']
    if submission_data['submissionState'] == 'FAILED' and 'error' in submission_data:
        patch_data['submissionError'] = submission_data['error']

    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': SUBMISSION_LISTENER_API_KEY,
        'X-Service-Role': SUBMISSION_LISTENER_SERVICE_ROLE,
        'User-Agent': 'submission-listener/1.0'
    }

    try:
        logger.info(f"Sending PATCH request to {url} for workflow {workflow_id}")
        response = requests.patch(url, json=patch_data, headers=headers, timeout=10)
        response.raise_for_status()
        logger.info(f"Successfully updated workflow {workflow_id} status to {submission_data['submissionState']}")
        return True
    except requests.exceptions.Timeout:
        logger.error(f"Timeout updating workflow {workflow_id}")
        return False
    except requests.exceptions.HTTPError as e:
        logger.error(f"HTTP error updating workflow {workflow_id}: {e.response.status_code} - {e.response.text}")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Request error updating workflow {workflow_id}: {str(e)}")
        return False

def validate_message(message, schema):
    """Validate message against the provided JSON schema."""
    try:
        jsonschema.validate(instance=message, schema=schema)
        logger.debug("Message validation passed")
        return True
    except jsonschema.exceptions.ValidationError as e:
        logger.error(f"Message validation failed: {str(e)}")
        return False

def callback(ch, method, properties, body):
    """Handle incoming messages from the RabbitMQ queue."""
    logger.info(f"Received message: {body.decode()}")
    
    try:
        message = json.loads(body)
        routing_key = getattr(method, 'routing_key', '') or ''

        # Route workflow messages
        is_workflow = routing_key in (
            'workflow.submitted', 'workflow.updated',
            RABBITMQ_QUEUE_WORKFLOW_SUBMITTED, RABBITMQ_QUEUE_WORKFLOW_UPDATED
        )

        if is_workflow:
            workflow_id = message.get('workflowId')
            if not workflow_id:
                logger.error("Missing workflowId in workflow message")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            logger.info(f"Processing workflow status update for ID: {workflow_id}")
            success = update_workflow_status(workflow_id, message)
        else:
            # Choose schema based on queue/topic
            if routing_key == 'artifact.updated' or routing_key.endswith('artifact.updated.queue'):
                schema = artifact_updated_schema
            else:
                schema = artifact_submitted_schema

            # Validate message against schema
            if not validate_message(message, schema):
                logger.error("Message validation failed, rejecting message")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return

            # Extract artifact ID and update status
            artifact_id = message['artifactId']
            logger.info(f"Processing artifact submission update for ID: {artifact_id}")
            success = update_artifact_status(artifact_id, message)
        
        msg_id = workflow_id if is_workflow else artifact_id
        if success:
            logger.info(f"Successfully processed {routing_key} message for {msg_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            logger.error(f"Failed to process {routing_key} message for {msg_id}, rejecting (not requeuing)")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in message: {str(e)}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except KeyError as e:
        logger.error(f"Missing required field in message: {str(e)}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except Exception as e:
        logger.error(f"Unexpected error processing message: {str(e)}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

def start_rabbitmq_consumer():
    """Connect to RabbitMQ and start consuming messages."""
    connection = None
    retry_count = 0
    max_retries = 30  # 2.5 minutes of retries
    
    while not connection and retry_count < max_retries:
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            ssl_options = None
            if RABBITMQ_TLS:
                ssl_options = pika.SSLOptions(ssl.create_default_context(), RABBITMQ_HOST)

            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials,
                ssl_options=ssl_options,
                heartbeat=600,
                connection_attempts=3,
                retry_delay=2
            )
            connection = pika.BlockingConnection(parameters)
            logger.info(f"Connected to RabbitMQ at {RABBITMQ_HOST}:{RABBITMQ_PORT}")
            
        except pika.exceptions.AMQPConnectionError as e:
            retry_count += 1
            logger.warning(f"Failed to connect to RabbitMQ (attempt {retry_count}/{max_retries}): {str(e)}")
            time.sleep(5)
    
    if not connection:
        logger.error("Failed to connect to RabbitMQ after maximum retries")
        return

    channel = connection.channel()
    
    # Ensure queues exist (in case they weren't created by definitions.json)
    channel.queue_declare(queue=RABBITMQ_QUEUE_SUBMITTED, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_UPDATED, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_WORKFLOW_SUBMITTED, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_WORKFLOW_UPDATED, durable=True)

    # Set QoS to process one message at a time
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=RABBITMQ_QUEUE_SUBMITTED, on_message_callback=callback)
    channel.basic_consume(queue=RABBITMQ_QUEUE_UPDATED, on_message_callback=callback)
    channel.basic_consume(queue=RABBITMQ_QUEUE_WORKFLOW_SUBMITTED, on_message_callback=callback)
    channel.basic_consume(queue=RABBITMQ_QUEUE_WORKFLOW_UPDATED, on_message_callback=callback)

    logger.info(f"Started consuming from queues: {RABBITMQ_QUEUE_SUBMITTED}, {RABBITMQ_QUEUE_UPDATED}, {RABBITMQ_QUEUE_WORKFLOW_SUBMITTED}, {RABBITMQ_QUEUE_WORKFLOW_UPDATED}")
    logger.info(f"Using API Gateway URL: {API_GATEWAY_URL}")
    logger.info(f"Service role: {SUBMISSION_LISTENER_SERVICE_ROLE}")
    
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        logger.info("Shutting down consumer...")
        channel.stop_consuming()
    except Exception as e:
        logger.error(f"Error in consumer: {str(e)}")
    finally:
        if connection and not connection.is_closed:
            connection.close()
            logger.info("RabbitMQ connection closed")

# Simple HTTP server for health checks
class HealthCheckHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            health_data = {
                "status": "healthy",
                "service": "submission-listener",
                "environment": "Docker" if IS_DOCKER else "Local",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rabbitmq": {
                    "host": RABBITMQ_HOST,
                    "port": RABBITMQ_PORT,
                    "queue": RABBITMQ_QUEUE_SUBMITTED
                },
                "api_gateway_url": API_GATEWAY_URL
            }
            self.wfile.write(json.dumps(health_data).encode())
        else:
            self.send_response(404)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'Not Found')
    
    def log_message(self, format, *args):
        # Suppress default HTTP server logging
        pass

def start_health_server():
    """Start a simple HTTP server for health checks."""
    try:
        server = http.server.HTTPServer(('0.0.0.0', 8000), HealthCheckHandler)
        logger.info("Health check server started on port 8000")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Error starting health server: {str(e)}")

if __name__ == "__main__":
    logger.info("Starting Submission Listener service...")
    
    # Start health check server in a separate thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Start RabbitMQ consumer (main thread)
    start_rabbitmq_consumer() 
        # Add a test line - Testing CI/CD pipeline
