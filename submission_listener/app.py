import json
import os
import pika
import requests
import logging
import jsonschema
import time
import http.server
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables - Updated to match API Gateway expectations
RABBITMQ_HOST = os.getenv('RABBITMQ_HOST', 'localhost')
RABBITMQ_PORT = int(os.getenv('RABBITMQ_PORT', 5672))
RABBITMQ_USER = os.getenv('RABBITMQ_USER', 'user')
RABBITMQ_PASS = os.getenv('RABBITMQ_PASS', 'password')
RABBITMQ_QUEUE = os.getenv('RABBITMQ_QUEUE', 'artifact.submitted.queue')
API_GATEWAY_URL = os.getenv('API_GATEWAY_URL', 'http://api-gateway:3000/api/artifacts')
API_KEY = os.getenv('API_KEY', 'a_random_key')
SERVICE_ROLE = os.getenv('SERVICE_ROLE', 'submitter_listener')

# Load schema for validation
with open('/app/schema/artifact.submitted.v1.schema.json', 'r') as f:
    artifact_submitted_schema = json.load(f)

def update_artifact_status(artifact_id, submission_data):
    """
    Send a PATCH request to the API Gateway to update an artifact's status.
    Uses API Key authentication with role-based access control.
    """
    url = f"{API_GATEWAY_URL}/{artifact_id}"
    
    # Prepare data for PATCH request based on the artifact.submitted message
    patch_data = {
        'submissionState': submission_data['submissionState'],
        'submittedAt': submission_data['submittedAt']
    }
    
    # Include blockchainTxId if present (should be there if submissionState is SUBMITTED)
    if 'blockchainTxId' in submission_data:
        patch_data['blockchainTxId'] = submission_data['blockchainTxId']
    
    # Add peerId if present
    if 'peerId' in submission_data:
        patch_data['peerId'] = submission_data['peerId']
    
    headers = {
        'Content-Type': 'application/json',
        'X-API-Key': API_KEY,
        'X-Service-Role': SERVICE_ROLE,
        'User-Agent': 'submission-listener/1.0'
    }
    
    try:
        logger.info(f"Sending PATCH request to {url} for artifact {artifact_id}")
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

def validate_message(message):
    """Validate message against the JSON schema."""
    try:
        jsonschema.validate(instance=message, schema=artifact_submitted_schema)
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
        
        # Validate message against schema
        if not validate_message(message):
            logger.error("Message validation failed, rejecting message")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        
        # Extract artifact ID and update status
        artifact_id = message['artifactId']
        logger.info(f"Processing artifact submission update for ID: {artifact_id}")
        
        success = update_artifact_status(artifact_id, message)
        
        if success:
            # Acknowledge the message
            logger.info(f"Successfully processed message for artifact {artifact_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # Negative acknowledge and requeue for retry (with exponential backoff)
            logger.warning(f"Failed to process message for artifact {artifact_id}, requeuing")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in message: {str(e)}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except KeyError as e:
        logger.error(f"Missing required field in message: {str(e)}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
    except Exception as e:
        logger.error(f"Unexpected error processing message: {str(e)}")
        ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

def start_rabbitmq_consumer():
    """Connect to RabbitMQ and start consuming messages."""
    # Retry connection if RabbitMQ is not immediately available
    connection = None
    retry_count = 0
    max_retries = 30  # 2.5 minutes of retries
    
    while not connection and retry_count < max_retries:
        try:
            credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASS)
            parameters = pika.ConnectionParameters(
                host=RABBITMQ_HOST,
                port=RABBITMQ_PORT,
                credentials=credentials,
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
    
    # Ensure queue exists (in case it wasn't created by definitions.json)
    channel.queue_declare(queue=RABBITMQ_QUEUE, durable=True)
    
    # Set QoS to process one message at a time
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=RABBITMQ_QUEUE, on_message_callback=callback)
    
    logger.info(f"Started consuming from queue: {RABBITMQ_QUEUE}")
    logger.info(f"Using API Gateway URL: {API_GATEWAY_URL}")
    logger.info(f"Service role: {SERVICE_ROLE}")
    
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
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rabbitmq_queue": RABBITMQ_QUEUE,
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