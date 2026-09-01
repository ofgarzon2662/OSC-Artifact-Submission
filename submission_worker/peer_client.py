import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)


class PeerClient:
    """Authenticated client for one organization-scoped Ledger Gateway."""

    def __init__(self, peer_url: str, token: str = "", timeout: int = 30):
        self.peer_url = peer_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.service_label = self._resolve_service_label(self.peer_url)
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "User-Agent": "osc-submission-worker/3.0",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"
        logger.info("Initialized %s client", self.service_label)

    @staticmethod
    def _resolve_service_label(peer_url: str) -> str:
        host = (urlparse(peer_url).hostname or "").strip().lower()
        return host or "ledger-gateway"

    @staticmethod
    def _error(message: str, retryable: bool) -> Dict[str, Any]:
        return {
            "success": False,
            "error": message[:512],
            "retryable": retryable,
            "timestamp": time.time(),
        }

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.peer_url}{path}"
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout)
            if response.status_code >= 400:
                retryable = (
                    response.status_code in (408, 425, 429)
                    or response.status_code >= 500
                )
                detail = ""
                try:
                    parsed = response.json()
                    if isinstance(parsed, dict):
                        detail = str(parsed.get("error") or parsed.get("message") or "")
                        retryable = bool(parsed.get("retryable", retryable))
                except ValueError:
                    pass
                suffix = f": {detail[:256]}" if detail else ""
                return self._error(
                    f"HTTP {response.status_code} from {self.service_label}{suffix}",
                    retryable,
                )

            result = response.json()
            if not isinstance(result, dict) or not isinstance(result.get("success"), bool):
                return self._error(
                    f"Invalid response from {self.service_label}", True
                )
            result.setdefault("retryable", False)
            return result
        except requests.exceptions.Timeout:
            return self._error(f"Timeout communicating with {self.service_label}", True)
        except requests.exceptions.ConnectionError:
            return self._error(
                f"Connection error communicating with {self.service_label}", True
            )
        except requests.exceptions.RequestException as error:
            logger.warning("Ledger Gateway request failed: %s", type(error).__name__)
            return self._error(
                f"Request error communicating with {self.service_label}", True
            )
        except ValueError:
            return self._error(f"Invalid response from {self.service_label}", True)

    def submit_artifact(self, command: Dict[str, Any]) -> Dict[str, Any]:
        return self._post("/submit", command)

    def update_artifact(
        self,
        artifact_id: str,
        patch: Dict[str, Any],
        organization: Optional[Dict[str, Any]] = None,
        contract_version: str = "v3",
        request: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._post(
            "/update",
            {
                "artifactId": artifact_id,
                "patch": patch,
                "contractVersion": contract_version,
                "organization": organization,
                "request": request,
                "correlationId": correlation_id,
            },
        )

    def submit_workflow(self, command: Dict[str, Any]) -> Dict[str, Any]:
        return self._post("/workflow/submit", command)

    def update_workflow(
        self,
        workflow_id: str,
        patch: Dict[str, Any],
        organization: Optional[Dict[str, Any]] = None,
        contract_version: str = "v3",
        request: Optional[Dict[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._post(
            "/workflow/update",
            {
                "workflowId": workflow_id,
                "patch": patch,
                "contractVersion": contract_version,
                "organization": organization,
                "request": request,
                "correlationId": correlation_id,
            },
        )

    def health_check(self) -> bool:
        try:
            response = self.session.get(f"{self.peer_url}/health", timeout=5)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    def close(self):
        self.session.close()
