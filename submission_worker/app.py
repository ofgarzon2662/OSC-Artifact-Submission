import json
import os
import ssl
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
RABBITMQ_TLS = os.getenv(
    'RABBITMQ_TLS', 'true' if RABBITMQ_PORT == 5671 else 'false'
).lower() == 'true'
RABBITMQ_QUEUE_SUBMIT = os.getenv('RABBITMQ_QUEUE_SUBMIT') or os.getenv('RABBITMQ_QUEUE_CREATED', 'artifact.submit.queue')
RABBITMQ_QUEUE_SUBMITTED = os.getenv('RABBITMQ_QUEUE_SUBMITTED', 'artifact.submitted.queue')
RABBITMQ_QUEUE_UPDATE = os.getenv('RABBITMQ_QUEUE_UPDATE', 'artifact.update.queue')
RABBITMQ_QUEUE_UPDATED = os.getenv('RABBITMQ_QUEUE_UPDATED', 'artifact.updated.queue')

# Workflow queues
RABBITMQ_QUEUE_WORKFLOW_SUBMIT = os.getenv('RABBITMQ_QUEUE_WORKFLOW_SUBMIT', 'workflow.submit.queue')
RABBITMQ_QUEUE_WORKFLOW_SUBMITTED = os.getenv('RABBITMQ_QUEUE_WORKFLOW_SUBMITTED', 'workflow.submitted.queue')
RABBITMQ_QUEUE_WORKFLOW_UPDATE = os.getenv('RABBITMQ_QUEUE_WORKFLOW_UPDATE', 'workflow.update.queue')
RABBITMQ_QUEUE_WORKFLOW_UPDATED = os.getenv('RABBITMQ_QUEUE_WORKFLOW_UPDATED', 'workflow.updated.queue')

# Upstream service configuration (adapter-compatible submit/update API)
if IS_DOCKER:
    FABRIC_BRIDGE_URL = os.getenv('FABRIC_BRIDGE_URL', 'http://fabric-bridge:4000')
else:
    FABRIC_BRIDGE_URL = os.getenv('FABRIC_BRIDGE_URL', 'http://localhost:4000')

# Log environment info
logger.info(f"Environment: {'Docker' if IS_DOCKER else 'Local'}")
logger.info(f"RabbitMQ: {RABBITMQ_HOST}:{RABBITMQ_PORT}")
logger.info(f"Upstream submit/update URL: {FABRIC_BRIDGE_URL}")
logger.info(f"Listening to queue: {RABBITMQ_QUEUE_SUBMIT}")
logger.info(f"Publishing to queue: {RABBITMQ_QUEUE_SUBMITTED}")
logger.info(f"Listening for updates on: {RABBITMQ_QUEUE_UPDATE}")

# Initialize upstream submit/update client
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
        
        # Include peerId if provided by bridge
        if submission_result.get('peerId'):
            message['peerId'] = submission_result['peerId']
        
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

def publish_artifact_updated(channel, artifact_id, update_result):
    """
    Publish an artifact.updated event to the message queue.
    """
    try:
        message = {
            'artifactId': artifact_id,
            'submissionState': 'SUCCESS' if update_result.get('success') else 'FAILED',
            'updatedAt': datetime.now(timezone.utc).isoformat(),
            'version': 'v1'
        }
        tx_id = (
            update_result.get('txId')
            or update_result.get('transactionId')
            or update_result.get('txID')
        )
        if update_result.get('success') and tx_id:
            message['blockchainTxId'] = tx_id
        if not update_result.get('success') and update_result.get('error'):
            message['error'] = update_result['error']

        channel.basic_publish(
            exchange='',
            routing_key=RABBITMQ_QUEUE_UPDATED,
            body=json.dumps(message),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type='application/json'
            )
        )
        logger.info(f"Published artifact.updated event for artifact {artifact_id} with state {message['submissionState']}")
    except Exception as e:
        logger.error(f"Failed to publish artifact.updated event for artifact {artifact_id}: {str(e)}")
        raise

def process_artifact_submission(channel, artifact_id, artifact_data):
    """
    Process an artifact submission by calling the upstream submit endpoint.
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
 
        # Sanitize optional array fields
        cleaned_dois = None
        if isinstance(artifact_data.get('dois'), list):
            cleaned_dois = [d for d in artifact_data.get('dois', []) if isinstance(d, str) and d.strip()]

        # Build payload for upstream submit/update API
        data_payload = {
            'manifest': manifest,
            'title': title,
            'description': artifact_data.get('description'),
            'submission_comment': artifact_data.get('submission_comment'),
            'contributor': artifact_data.get('contributor'),
            'keywords': artifact_data.get('keywords'),
            'links': artifact_data.get('links'),
            # 'dois' will be added only if non-empty list
            'fundingAgencies': artifact_data.get('fundingAgencies'),
            'acknowledgements': artifact_data.get('acknowledgements'),
            'footprint': footprint
        }
        if cleaned_dois:
            data_payload['dois'] = cleaned_dois

        # Call upstream submit endpoint. Map message to expected payload
        submission_result = peer_client.submit_artifact({
            'artifactId': artifact_id,
            'data': data_payload,
            'contractVersion': artifact_data.get('contractVersion', 'v1'),
            'organization': artifact_data.get('organization'),
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

def process_artifact_update(channel, artifact_id, patch_data):
    """
    Process an artifact update by calling upstream /update.
    """
    logger.info(f"Processing artifact update for ID: {artifact_id}")
    try:
        message_metadata = patch_data if isinstance(patch_data, dict) else {}
        # Support both shapes: {artifactId, patch:{...}} and flat {artifactId, ...fields}
        effective_patch = None
        if isinstance(patch_data, dict) and isinstance(patch_data.get('patch'), dict):
            effective_patch = patch_data.get('patch')
        elif isinstance(patch_data, dict):
            routing_keys = {'artifactId', 'contractVersion', 'organization'}
            effective_patch = {
                k: v for k, v in patch_data.items() if k not in routing_keys
            }
        else:
            effective_patch = {}

        fp = effective_patch.get('footprint')
        if fp is not None and not (isinstance(fp, str) and len(fp) == 64 and all(c in '0123456789abcdefABCDEF' for c in fp)):
            error_msg = f"Invalid 'footprint' in update for artifact {artifact_id}"
            logger.error(error_msg)
            publish_artifact_updated(channel, artifact_id, { 'success': False, 'error': error_msg })
            return False

        update_result = peer_client.update_artifact(
            artifact_id,
            effective_patch,
            organization=message_metadata.get('organization'),
            contract_version=message_metadata.get('contractVersion', 'v1'),
        )
        logger.info(f"Peer update result for artifact {artifact_id}: {update_result}")
        publish_artifact_updated(channel, artifact_id, update_result)
        return True
    except Exception as e:
        logger.error(f"Error processing update for artifact {artifact_id}: {str(e)}")
        publish_artifact_updated(channel, artifact_id, { 'success': False, 'error': f"Update processing failed: {str(e)}" })
        return False

def publish_workflow_submitted(channel, workflow_id, submission_result):
    """Publish a workflow.submitted event to the message queue."""
    try:
        message = {
            'workflowId': workflow_id,
            'submissionState': 'SUCCESS' if submission_result.get('success') else 'FAILED',
            'submittedAt': datetime.now(timezone.utc).isoformat(),
            'version': 'v1'
        }
        tx_id = (
            submission_result.get('txId')
            or submission_result.get('transactionId')
            or submission_result.get('txID')
        )
        if submission_result.get('success') and tx_id:
            message['blockchainTxId'] = tx_id
        if submission_result.get('peerId'):
            message['peerId'] = submission_result['peerId']
        if not submission_result.get('success') and submission_result.get('error'):
            message['error'] = submission_result['error']

        channel.basic_publish(
            exchange='',
            routing_key=RABBITMQ_QUEUE_WORKFLOW_SUBMITTED,
            body=json.dumps(message),
            properties=pika.BasicProperties(delivery_mode=2, content_type='application/json')
        )
        logger.info(f"Published workflow.submitted event for workflow {workflow_id} with state {message['submissionState']}")
    except Exception as e:
        logger.error(f"Failed to publish workflow.submitted event for workflow {workflow_id}: {str(e)}")
        raise

def publish_workflow_updated(channel, workflow_id, update_result):
    """Publish a workflow.updated event to the message queue."""
    try:
        message = {
            'workflowId': workflow_id,
            'submissionState': 'SUCCESS' if update_result.get('success') else 'FAILED',
            'updatedAt': datetime.now(timezone.utc).isoformat(),
            'version': 'v1'
        }
        tx_id = (
            update_result.get('txId')
            or update_result.get('transactionId')
            or update_result.get('txID')
        )
        if update_result.get('success') and tx_id:
            message['blockchainTxId'] = tx_id
        if not update_result.get('success') and update_result.get('error'):
            message['error'] = update_result['error']

        channel.basic_publish(
            exchange='',
            routing_key=RABBITMQ_QUEUE_WORKFLOW_UPDATED,
            body=json.dumps(message),
            properties=pika.BasicProperties(delivery_mode=2, content_type='application/json')
        )
        logger.info(f"Published workflow.updated event for workflow {workflow_id} with state {message['submissionState']}")
    except Exception as e:
        logger.error(f"Failed to publish workflow.updated event for workflow {workflow_id}: {str(e)}")
        raise

def process_workflow_submission(channel, workflow_id, workflow_data):
    """Process a workflow submission by calling the upstream /workflow/submit endpoint."""
    logger.info(f"Processing workflow submission for ID: {workflow_id}")
    try:
        data_payload = {
            'title': workflow_data.get('title'),
            'description': workflow_data.get('description'),
            'submission_comment': workflow_data.get('submission_comment'),
            'keywords': workflow_data.get('keywords'),
            'artifact_ids': workflow_data.get('artifactIds', []),
            'github_repositories': workflow_data.get('githubRepositories', []),
            'contributor': workflow_data.get('contributor'),
        }

        submission_result = peer_client.submit_workflow({
            'workflowId': workflow_id,
            'data': data_payload,
            'contractVersion': workflow_data.get('contractVersion', 'v1'),
            'organization': workflow_data.get('organization'),
        })
        logger.info(f"Peer submission result for workflow {workflow_id}: {submission_result}")
        publish_workflow_submitted(channel, workflow_id, submission_result)
        return True
    except Exception as e:
        logger.error(f"Error processing workflow {workflow_id}: {str(e)}")
        publish_workflow_submitted(channel, workflow_id, {
            'success': False,
            'error': f"Submission processing failed: {str(e)}"
        })
        return False

def process_workflow_update(channel, workflow_id, patch_data):
    """Process a workflow update by calling upstream /workflow/update."""
    logger.info(f"Processing workflow update for ID: {workflow_id}")
    try:
        message_metadata = patch_data if isinstance(patch_data, dict) else {}
        effective_patch = None
        if isinstance(patch_data, dict) and isinstance(patch_data.get('patch'), dict):
            effective_patch = patch_data.get('patch')
        elif isinstance(patch_data, dict):
            routing_keys = {'workflowId', 'contractVersion', 'organization'}
            effective_patch = {
                k: v for k, v in patch_data.items() if k not in routing_keys
            }
        else:
            effective_patch = {}

        # Normalize camelCase keys to snake_case for the adapter
        normalized_patch = {
            'title': effective_patch.get('title'),
            'description': effective_patch.get('description'),
            'submission_comment': effective_patch.get('submission_comment'),
            'keywords': effective_patch.get('keywords'),
            'artifact_ids': effective_patch.get('artifactIds', effective_patch.get('artifact_ids', [])),
            'github_repositories': effective_patch.get('githubRepositories', effective_patch.get('github_repositories', [])),
            'contributor': effective_patch.get('contributor'),
        }

        update_result = peer_client.update_workflow(
            workflow_id,
            normalized_patch,
            organization=message_metadata.get('organization'),
            contract_version=message_metadata.get('contractVersion', 'v1'),
        )
        logger.info(f"Peer update result for workflow {workflow_id}: {update_result}")
        publish_workflow_updated(channel, workflow_id, update_result)
        return True
    except Exception as e:
        logger.error(f"Error processing update for workflow {workflow_id}: {str(e)}")
        publish_workflow_updated(channel, workflow_id, {'success': False, 'error': f"Update processing failed: {str(e)}"})
        return False

def callback(ch, method, properties, body):
    """Handle incoming artifact.submit and workflow.submit messages from the RabbitMQ queue."""
    logger.info(f"Received message on {getattr(method, 'routing_key', '')}: {body.decode()}")
    
    try:
        message = json.loads(body)
        
        rk = getattr(method, 'routing_key', '') or ''

        # Route workflow messages
        if rk in ('workflow.submit', RABBITMQ_QUEUE_WORKFLOW_SUBMIT):
            workflow_id = message.get('workflowId')
            if not workflow_id:
                logger.error("Missing workflowId in workflow.submit message")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            success = process_workflow_submission(ch, workflow_id, message)
            msg_id = workflow_id
        elif rk in ('workflow.update', RABBITMQ_QUEUE_WORKFLOW_UPDATE):
            workflow_id = message.get('workflowId')
            if not workflow_id:
                logger.error("Missing workflowId in workflow.update message")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            success = process_workflow_update(ch, workflow_id, message)
            msg_id = workflow_id
        else:
            # Artifact messages
            artifact_id = message.get('artifactId')
            if not artifact_id:
                logger.error("Missing artifactId in message")
                ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)
                return
            if rk == 'artifact.update' or rk == RABBITMQ_QUEUE_UPDATE:
                success = process_artifact_update(ch, artifact_id, message)
            else:
                success = process_artifact_submission(ch, artifact_id, message)
            msg_id = artifact_id

        if success:
            logger.info(f"Successfully processed {rk} message for {msg_id}")
            ch.basic_ack(delivery_tag=method.delivery_tag)
        else:
            logger.error(f"Failed to process {rk} message for {msg_id}, rejecting (not requeuing)")
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
    """Connect to RabbitMQ and start consuming artifact.submit and artifact.update messages."""
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
    
    # Ensure queues exist
    channel.queue_declare(queue=RABBITMQ_QUEUE_SUBMIT, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_SUBMITTED, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_UPDATE, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_UPDATED, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_WORKFLOW_SUBMIT, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_WORKFLOW_SUBMITTED, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_WORKFLOW_UPDATE, durable=True)
    channel.queue_declare(queue=RABBITMQ_QUEUE_WORKFLOW_UPDATED, durable=True)

    # Set QoS to process one message at a time
    channel.basic_qos(prefetch_count=1)
    channel.basic_consume(queue=RABBITMQ_QUEUE_SUBMIT, on_message_callback=callback)
    channel.basic_consume(queue=RABBITMQ_QUEUE_UPDATE, on_message_callback=callback)
    channel.basic_consume(queue=RABBITMQ_QUEUE_WORKFLOW_SUBMIT, on_message_callback=callback)
    channel.basic_consume(queue=RABBITMQ_QUEUE_WORKFLOW_UPDATE, on_message_callback=callback)

    logger.info(f"Started consuming from queues: {RABBITMQ_QUEUE_SUBMIT}, {RABBITMQ_QUEUE_UPDATE}, {RABBITMQ_QUEUE_WORKFLOW_SUBMIT}, {RABBITMQ_QUEUE_WORKFLOW_UPDATE}")
    logger.info(f"Ready to process artifact submissions and updates...")
    
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
