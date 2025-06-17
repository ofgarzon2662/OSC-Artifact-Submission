import requests
import logging
import time
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PeerClient:
    """
    Client for communicating with the blockchain peer (mock or real).
    Handles artifact submission and error handling.
    """
    
    def __init__(self, peer_url: str, timeout: int = 30):
        """
        Initialize the peer client.
        
        Args:
            peer_url: Base URL of the peer service
            timeout: Request timeout in seconds
        """
        self.peer_url = peer_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        
        # Set common headers
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'submission-worker/1.0'
        })
        
        logger.info(f"Initialized PeerClient with URL: {self.peer_url}")
    
    def submit_artifact(self, artifact_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit an artifact to the blockchain peer.
        
        Args:
            artifact_data: Dictionary containing artifact information
            
        Returns:
            Dictionary with submission result:
            {
                'success': bool,
                'txId': str (if success),
                'peerId': str (optional),
                'error': str (if failure),
                'timestamp': str
            }
        """
        url = f"{self.peer_url}/submit-artifact"
        
        try:
            logger.info(f"Submitting artifact {artifact_data.get('artifactId')} to peer at {url}")
            
            response = self.session.post(
                url,
                json=artifact_data,
                timeout=self.timeout
            )
            
            # Log response for debugging
            logger.info(f"Peer response status: {response.status_code}")
            logger.debug(f"Peer response body: {response.text}")
            
            response.raise_for_status()
            
            # Parse response
            result = response.json()
            
            # Validate response format
            if not isinstance(result, dict):
                raise ValueError("Invalid response format from peer")
            
            # Ensure required fields are present
            if 'success' not in result:
                raise ValueError("Missing 'success' field in peer response")
            
            logger.info(f"Artifact {artifact_data.get('artifactId')} submission result: {result.get('success')}")
            
            return result
            
        except requests.exceptions.Timeout:
            error_msg = f"Timeout communicating with peer at {url}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except requests.exceptions.ConnectionError:
            error_msg = f"Connection error communicating with peer at {url}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error {e.response.status_code} from peer: {e.response.text}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except (ValueError, KeyError) as e:
            error_msg = f"Invalid response from peer: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except Exception as e:
            error_msg = f"Unexpected error communicating with peer: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
    
    def health_check(self) -> bool:
        """
        Check if the peer service is healthy.
        
        Returns:
            True if peer is healthy, False otherwise
        """
        try:
            url = f"{self.peer_url}/health"
            response = self.session.get(url, timeout=5)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"Peer health check failed: {str(e)}")
            return False
    
    def close(self):
        """Close the session and cleanup resources."""
        self.session.close()
        logger.info("PeerClient session closed") 