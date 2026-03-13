import requests
import logging
import time
from urllib.parse import urlparse
from typing import Dict, Any

logger = logging.getLogger(__name__)

class PeerClient:
    """
    Client for communicating with the upstream submit/update service.
    Handles artifact submission and error handling.
    """
    
    def __init__(self, peer_url: str, timeout: int = 30):
        """
        Initialize the peer client.
        
        Args:
            peer_url: Base URL of the upstream service
            timeout: Request timeout in seconds
        """
        self.peer_url = peer_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()
        self.service_label = self._resolve_service_label(self.peer_url)
        
        # Set common headers
        self.session.headers.update({
            'Content-Type': 'application/json',
            'User-Agent': 'submission-worker/1.0'
        })
        
        logger.info(f"Initialized {self.service_label} client with URL: {self.peer_url}")

    @staticmethod
    def _resolve_service_label(peer_url: str) -> str:
        parsed_url = urlparse(peer_url)
        host = (parsed_url.hostname or "").strip().lower()

        if not host:
            return "upstream-service"
        if host == "adapter":
            return "adapter"
        if host == "fabric-bridge":
            return "fabric-bridge"
        return host
    
    def submit_artifact(self, artifact_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit an artifact to the blockchain peer.
        
        Args:
            artifact_data: Dictionary containing artifact information. Expected shape:
                {
                  'artifactId': str,
                  'data': {...}
                }
            
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
        url = f"{self.peer_url}/submit"
        
        try:
            logger.info(
                f"Submitting artifact {artifact_data.get('artifactId')} to "
                f"{self.service_label} at {url}"
            )
            
            response = self.session.post(
                url,
                json=artifact_data,
                timeout=self.timeout
            )
            
            # Log response for debugging
            logger.info(f"{self.service_label} response status: {response.status_code}")
            logger.debug(f"{self.service_label} response body: {response.text}")
            
            response.raise_for_status()
            
            # Parse response
            result = response.json()
            
            # Validate response format
            if not isinstance(result, dict):
                raise ValueError(f"Invalid response format from {self.service_label}")
            
            # Ensure required fields are present
            if 'success' not in result:
                raise ValueError(f"Missing 'success' field in {self.service_label} response")
            
            logger.info(f"Artifact {artifact_data.get('artifactId')} submission result: {result.get('success')}")
            
            return result
            
        except requests.exceptions.Timeout:
            error_msg = f"Timeout communicating with {self.service_label} at {url}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except requests.exceptions.ConnectionError:
            error_msg = f"Connection error communicating with {self.service_label} at {url}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error {e.response.status_code} from {self.service_label}: {e.response.text}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except (ValueError, KeyError) as e:
            error_msg = f"Invalid response from {self.service_label}: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }
            
        except Exception as e:
            error_msg = f"Unexpected error communicating with {self.service_label}: {str(e)}"
            logger.error(error_msg)
            return {
                'success': False,
                'error': error_msg,
                'timestamp': time.time()
            }

    def update_artifact(self, artifact_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit an update for an artifact to the upstream service.

        Args:
            artifact_id: UUID string of the artifact to update
            patch: Dictionary containing partial fields to update (no title/description)

        Returns:
            Dictionary with update result similar to submit response
        """
        url = f"{self.peer_url}/update"

        try:
            logger.info(f"Updating artifact {artifact_id} via {self.service_label} at {url}")

            response = self.session.post(
                url,
                json={ 'artifactId': artifact_id, 'patch': patch },
                timeout=self.timeout
            )

            logger.info(f"{self.service_label} update response status: {response.status_code}")
            logger.debug(f"{self.service_label} update response body: {response.text}")

            response.raise_for_status()

            result = response.json()
            if not isinstance(result, dict) or 'success' not in result:
                raise ValueError(f"Invalid response from {self.service_label} for update")
            logger.info(f"Artifact {artifact_id} update result: {result.get('success')}")
            return result

        except requests.exceptions.Timeout:
            error_msg = f"Timeout communicating with {self.service_label} at {url}"
            logger.error(error_msg)
            return { 'success': False, 'error': error_msg, 'timestamp': time.time() }
        except requests.exceptions.ConnectionError:
            error_msg = f"Connection error communicating with {self.service_label} at {url}"
            logger.error(error_msg)
            return { 'success': False, 'error': error_msg, 'timestamp': time.time() }
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error {e.response.status_code} from {self.service_label}: {e.response.text}"
            logger.error(error_msg)
            return { 'success': False, 'error': error_msg, 'timestamp': time.time() }
        except (ValueError, KeyError) as e:
            error_msg = f"Invalid response from {self.service_label}: {str(e)}"
            logger.error(error_msg)
            return { 'success': False, 'error': error_msg, 'timestamp': time.time() }
        except Exception as e:
            error_msg = f"Unexpected error communicating with {self.service_label}: {str(e)}"
            logger.error(error_msg)
            return { 'success': False, 'error': error_msg, 'timestamp': time.time() }
    
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