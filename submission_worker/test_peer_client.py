from unittest.mock import MagicMock, patch

import requests

from peer_client import PeerClient


def client():
    return PeerClient(
        "http://ledger-gateway-nsg:4000", "internal-token-value-000000001"
    )


def test_client_sends_internal_bearer_token_and_disables_proxy_inheritance():
    value = client()
    assert value.session.headers["Authorization"].startswith("Bearer ")
    assert value.session.trust_env is False


def test_success_response_is_returned():
    value = client()
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True, "txId": "tx-001"}
    with patch.object(value.session, "post", return_value=response) as post:
        result = value.submit_artifact({"artifactId": "id"})
    assert result == {"success": True, "txId": "tx-001", "retryable": False}
    assert post.call_args.kwargs["timeout"] == 30


def test_timeout_and_connection_errors_are_retryable():
    value = client()
    with patch.object(value.session, "post", side_effect=requests.Timeout):
        assert value.submit_artifact({})["retryable"] is True
    with patch.object(value.session, "post", side_effect=requests.ConnectionError):
        assert value.submit_artifact({})["retryable"] is True


def test_http_classification_distinguishes_poison_from_outage():
    value = client()
    bad_request = MagicMock(status_code=400)
    bad_request.json.return_value = {"message": "invalid command"}
    unavailable = MagicMock(status_code=502)
    unavailable.json.return_value = {"error": "peer down", "retryable": True}
    with patch.object(value.session, "post", side_effect=[bad_request, unavailable]):
        permanent = value.submit_artifact({})
        transient = value.submit_artifact({})
    assert permanent["retryable"] is False
    assert transient["retryable"] is True
    assert "peer down" in transient["error"]


def test_invalid_response_is_retryable():
    value = client()
    response = MagicMock(status_code=200)
    response.json.return_value = ["not", "an", "object"]
    with patch.object(value.session, "post", return_value=response):
        result = value.submit_artifact({})
    assert result["success"] is False
    assert result["retryable"] is True


def test_update_payload_preserves_request_metadata():
    value = client()
    response = MagicMock(status_code=200)
    response.json.return_value = {"success": True}
    request_metadata = {"operation": "artifact.update"}
    with patch.object(value.session, "post", return_value=response) as post:
        value.update_artifact(
            "artifact-id",
            {"keywords": ["provenance"]},
            organization={"id": "nsg"},
            contract_version="v3",
            request=request_metadata,
            correlation_id="corr-001",
        )
    payload = post.call_args.kwargs["json"]
    assert payload["request"] == request_metadata
    assert payload["correlationId"] == "corr-001"


def test_health_check_returns_false_on_request_failure():
    value = client()
    with patch.object(value.session, "get", side_effect=requests.RequestException):
        assert value.health_check() is False
