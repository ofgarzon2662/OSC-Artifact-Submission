import pytest
from unittest.mock import patch, MagicMock
import app
import json

@pytest.fixture
def mock_channel():
    return MagicMock()

@pytest.fixture
def artifact_id():
    return "artifact-123"

@pytest.fixture
def submission_result_success():
    return {"success": True, "txId": "tx-1", "peerId": "peer-1"}

@pytest.fixture
def submission_result_failure():
    return {"success": False, "error": "fail"}

def test_publish_artifact_submitted_success(mock_channel, artifact_id, submission_result_success):
    app.publish_artifact_submitted(mock_channel, artifact_id, submission_result_success)
    args, kwargs = mock_channel.basic_publish.call_args
    body = json.loads(kwargs["body"])
    assert body["artifactId"] == artifact_id
    assert body["submissionState"] == "SUCCESS"
    assert body["blockchainTxId"] == "tx-1"
    assert body["peerId"] == "peer-1"
    assert "error" not in body

def test_publish_artifact_submitted_failure(mock_channel, artifact_id, submission_result_failure):
    app.publish_artifact_submitted(mock_channel, artifact_id, submission_result_failure)
    args, kwargs = mock_channel.basic_publish.call_args
    body = json.loads(kwargs["body"])
    assert body["artifactId"] == artifact_id
    assert body["submissionState"] == "FAILED"
    assert body["error"] == "fail"
    assert "blockchainTxId" not in body

def test_process_artifact_submission_success(mock_channel, artifact_id):
    with patch.object(app.peer_client, "submit_artifact", return_value={"success": True, "txId": "tx-1"}) as mock_submit:
        with patch("app.publish_artifact_submitted") as mock_publish:
            result = app.process_artifact_submission(mock_channel, artifact_id, {"foo": "bar"})
            assert result is True
            mock_submit.assert_called_once()
            mock_publish.assert_called_once()

def test_process_artifact_submission_failure(mock_channel, artifact_id):
    with patch.object(app.peer_client, "submit_artifact", side_effect=Exception("fail")) as mock_submit:
        with patch("app.publish_artifact_submitted") as mock_publish:
            result = app.process_artifact_submission(mock_channel, artifact_id, {"foo": "bar"})
            assert result is False
            mock_submit.assert_called_once()
            mock_publish.assert_called_once()

def test_callback_success(mock_channel, artifact_id):
    message = {"artifactId": artifact_id, "foo": "bar"}
    body = json.dumps(message).encode()
    method = MagicMock()
    method.delivery_tag = 1
    with patch("app.process_artifact_submission", return_value=True):
        app.callback(mock_channel, method, None, body)
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=1)

def test_callback_failure(mock_channel, artifact_id):
    message = {"artifactId": artifact_id, "foo": "bar"}
    body = json.dumps(message).encode()
    method = MagicMock()
    method.delivery_tag = 2
    with patch("app.process_artifact_submission", return_value=False):
        app.callback(mock_channel, method, None, body)
        mock_channel.basic_nack.assert_called_once_with(delivery_tag=2, requeue=False)

def test_callback_invalid_json(mock_channel):
    body = b"not json"
    method = MagicMock()
    method.delivery_tag = 3
    app.callback(mock_channel, method, None, body)
    mock_channel.basic_nack.assert_called_once_with(delivery_tag=3, requeue=False)

def test_callback_missing_artifact_id(mock_channel):
    message = {"foo": "bar"}
    body = json.dumps(message).encode()
    method = MagicMock()
    method.delivery_tag = 4
    app.callback(mock_channel, method, None, body)
    mock_channel.basic_nack.assert_called_once_with(delivery_tag=4, requeue=False) 