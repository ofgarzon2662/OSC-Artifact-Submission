import json
import os
import pika
import logging
import time
import asyncio
import threading
import http.server
from datetime import datetime, timezone
from dotenv import load_dotenv
from peer_client import PeerClient

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
RABBITMQ_QUEUE_SUBMIT = os.getenv('RABBITMQ_QUEUE_SUBMIT') or os.getenv('RABBITMQ_QUEUE_CREATED', 'artifact.submit.queue')
RABBITMQ_QUEUE_SUBMITTED = os.getenv('RABBITMQ_QUEUE_SUBMITTED', 'artifact.submitted.queue')

# Fabric Bridge Configuration (replaces mock peer)
if IS_DOCKER:
    FABRIC_BRIDGE_URL = os.getenv('FABRIC_BRIDGE_URL', 'http://fabric-bridge:4000')
else:
    FABRIC_BRIDGE_URL = os.getenv('FABRIC_BRIDGE_URL', 'http://localhost:4000')

# Log environment info
logger.info(f"Environment: {'Docker' if IS_DOCKER else 'Local'}")
logger.info(f"RabbitMQ: {RABBITMQ_HOST}:{RABBITMQ_PORT}")
logger.info(f"Fabric Bridge URL: {FABRIC_BRIDGE_URL}")
logger.info(f"Listening to queue: {RABBITMQ_QUEUE_SUBMIT}")
logger.info(f"Publishing to queue: {RABBITMQ_QUEUE_SUBMITTED}")

# Initialize fabric-bridge client
peer_client = PeerClient(FABRIC_BRIDGE_URL)

def publish_artifact_submitted(channel, artifact_id, submission_result):
    """
    Publish an artifact.submitted event to the message queue.
    """
    try:
        message = {
            'artifactId': artifact_id,
            'submissionState': 'SUCCESS' if submission_result.get('success') else 'FAILED',
            'submittedAt': datetime.now(timezone.utc).isoformat(),
            'version': 'v1'
        }
        
        # Add blockchain transaction ID if successful
        tx_id = (
            submission_result.get('txId')
            or submission_result.get('transactionId')
            or submission_result.get('txID')
        )
        if submission_result.get('success') and tx_id:
            message['blockchainTxId'] = tx_id
        
        # Do not include peerId; it's not required
        
        # Add error if failed
        if not submission_result.get('success') and submission_result.get('error'):
            message['error'] = submission_result['error']
        
        # Publish to the submitted queue
        channel.basic_publish(
            exchange='',
            routing_key=RABBITMQ_QUEUE_SUBMITTED,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,  # Make message persistent
                content_type='application/json'
            )
        )
        
        logger.info(f"Published artifact.submitted event for artifact {artifact_id} with state {message['submissionState']}")
        
    except Exception as e:
        logger.error(f"Failed to publish artifact.submitted event for artifact {artifact_id}: {str(e)}")
        raise

def process_artifact_submission(channel, artifact_id, artifact_data):
    """
    Process an artifact submission by calling fabric-bridge.
    """
    logger.info(f"Processing artifact submission for ID: {artifact_id}")
    
    try:
        # Extract manifest and title from the artifact data
        manifest = artifact_data.get('manifest')
        title = artifact_data.get('title')
        footprint = artifact_data.get('footprint')

        # Validate that the manifest exists
        if manifest is None:
            error_msg = f"Missing 'manifest' in message for artifact {artifact_id}"
            logger.error(error_msg)
            # Publish failure event
            publish_artifact_submitted(channel, artifact_id, {
                'success': False,
                'error': error_msg
            })
            return False

        # Validate that the footprint exists and is a 64-character hex string
        if footprint is None or not (isinstance(footprint, str) and len(footprint) == 64 and all(c in '0123456789abcdefABCDEF' for c in footprint)):
            error_msg = f"Missing or invalid 'footprint' in message for artifact {artifact_id}"
            logger.error(error_msg)
            # Publish failure event
            publish_artifact_submitted(channel, artifact_id, {
                'success': False,
                'error': error_msg
            })
            return False
 
        # Call fabric-bridge to submit the artifact. Map message to expected payload
        submission_result = peer_client.submit_artifact({
            'artifactId': artifact_id,
            'data': {
                'manifest': manifest,
                'title': title,
                'description': artifact_data.get('description'),
                'keywords': artifact_data.get('keywords'),
                'links': artifact_data.get('links'),
                'dois': artifact_data.get('dois'),
                'fundingAgencies': artifact_data.get('fundingAgencies'),
                'acknowledgements': artifact_data.get('acknowledgements'),
                'footprint': footprint
            }
        })
        
        logger.info(f"Peer submission result for artifact {artifact_id}: {submission_result}")
        
        # Publish the result event
        publish_artifact_submitted(channel, artifact_id, submission_result)
        
        return True
        
    except Exception as e:
        logger.error(f"Error processing artifact {artifact_id}: {str(e)}")
        
        # Publish failure event
        failure_result = {
            'success': False,
            'error': f"Submission processing failed: {str(e)}"
        }
        publish_artifact_submitted(channel, artifact_id, failure_result)
        
        return False

def callback(ch, method, properties, body):
    """Handle incoming artifact.submit messages from the RabbitMQ queue."""
    logger.info(f"Received artifact.submit message: {body.decode()}")
    
    try:
        message = json.loads(body)
        
        # Extract artifact information
        artifact_id = message.get('artifactId')
        if not artifact_id:
            logger.error("Missing artifactId in message")
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
            return
        
        # Process the artifact submission
        success = process_artifact_submission(ch, artifact_id, message)
        
        if success:
            # Acknowledge the message
            logger.info(f"Successfully processed artifact.submit message for artifact {artifact_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            # Don't requeue - we already published a failure event
            logger.error(f"Failed to process artifact.submit message for artifact {artifact_id}, rejecting (not requeuing)")
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
    """Connect to RabbitMQ and start consuming artifact.submit messages."""
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
    
    # Ensure both queues exist
    channel.queue_declare(queue=RABBITMQ_QUEUE_SUBMIT, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_SUBMITTED, durable=True)
    
    # Set QoS to process one message at a time
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=RABBITMQ_QUEUE_SUBMIT, on_message_callback=callback)
    
    logger.info(f"Started consuming from queue: {RABBITMQ_QUEUE_SUBMIT}")
    logger.info(f"Ready to process artifact submissions...")
    
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
                "service": "submission-worker",
                "environment": "Docker" if IS_DOCKER else "Local",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "rabbitmq": {
                    "host": RABBITMQ_HOST,
                    "port": RABBITMQ_PORT,
                    "queue_submit": RABBITMQ_QUEUE_SUBMIT,
                    "queue_submitted": RABBITMQ_QUEUE_SUBMITTED
                },
                "fabric_bridge_url": FABRIC_BRIDGE_URL
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
    logger.info("Starting Submission Worker service...")
    
    # Start health check server in a separate thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Start RabbitMQ consumer (main thread)
    start_rabbitmq_consumer() 