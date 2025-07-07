import pytest
from unittest.mock import patch, MagicMock
from peer_client import PeerClient
import requests

@pytest.fixture
def peer_client():
    return PeerClient("http://mock-peer:8080")

def test_submit_artifact_success(peer_client):
    artifact_data = {"artifactId": "abc123", "data": {}}
    response_data = {"success": True, "txId": "tx-1", "peerId": "peer-1"}
    with patch.object(peer_client.session, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp
        result = peer_client.submit_artifact(artifact_data)
        assert result["success"] is True
        assert result["txId"] == "tx-1"
        assert result["peerId"] == "peer-1"

def test_submit_artifact_failure(peer_client):
    artifact_data = {"artifactId": "abc123", "data": {}}
    response_data = {"success": False, "error": "fail"}
    with patch.object(peer_client.session, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 400
        mock_resp.json.return_value = response_data
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(response=mock_resp)
        mock_resp.text = "fail"
        mock_post.return_value = mock_resp
        result = peer_client.submit_artifact(artifact_data)
        assert result["success"] is False
        assert "error" in result

def test_submit_artifact_timeout(peer_client):
    artifact_data = {"artifactId": "abc123", "data": {}}
    with patch.object(peer_client.session, "post", side_effect=requests.exceptions.Timeout):
        result = peer_client.submit_artifact(artifact_data)
        assert result["success"] is False
        assert "timeout" in result["error"].lower()

def test_submit_artifact_connection_error(peer_client):
    artifact_data = {"artifactId": "abc123", "data": {}}
    with patch.object(peer_client.session, "post", side_effect=requests.exceptions.ConnectionError):
        result = peer_client.submit_artifact(artifact_data)
        assert result["success"] is False
        assert "connection error" in result["error"].lower()

def test_submit_artifact_invalid_response(peer_client):
    artifact_data = {"artifactId": "abc123", "data": {}}
    with patch.object(peer_client.session, "post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = ["not", "a", "dict"]
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp
        result = peer_client.submit_artifact(artifact_data)
        assert result["success"] is False
        assert "invalid response" in result["error"].lower()

def test_health_check_healthy(peer_client):
    with patch.object(peer_client.session, "get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_get.return_value = mock_resp
        assert peer_client.health_check() is True

def test_health_check_unhealthy(peer_client):
    with patch.object(peer_client.session, "get", side_effect=requests.exceptions.RequestException):
        assert peer_client.health_check() is False 