import pytest
from unittest.mock import patch, MagicMock
import app
import json
import io
import http.server

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

@pytest.fixture
def mock_artifact_data():
    return {
        "title": "Test Artifact",
        "manifest": [{"filename": "file1.txt", "hash": "123"}],
        "footprint": "f" * 64
    }

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

def test_process_artifact_submission_success(mock_channel, artifact_id, mock_artifact_data):
    with patch.object(app.peer_client, "submit_artifact", return_value={"success": True, "txId": "tx-1"}) as mock_submit:
        with patch("app.publish_artifact_submitted") as mock_publish:
            result = app.process_artifact_submission(mock_channel, artifact_id, mock_artifact_data)
            assert result is True
            mock_submit.assert_called_once()
            mock_publish.assert_called_once()

def test_process_artifact_submission_failure(mock_channel, artifact_id, mock_artifact_data):
    with patch.object(app.peer_client, "submit_artifact", side_effect=Exception("fail")) as mock_submit:
        with patch("app.publish_artifact_submitted") as mock_publish:
            result = app.process_artifact_submission(mock_channel, artifact_id, mock_artifact_data)
            assert result is False
            mock_submit.assert_called_once()
            mock_publish.assert_called_once()

def test_process_artifact_submission_missing_manifest(mock_channel, artifact_id):
    """Test submission processing when manifest is missing from the message."""
    with patch("app.publish_artifact_submitted") as mock_publish:
        result = app.process_artifact_submission(mock_channel, artifact_id, {"title": "no manifest here"})
        assert result is False
        mock_publish.assert_called_once()
        args, kwargs = mock_publish.call_args
        assert args[2]['success'] is False
        assert "Missing 'manifest'" in args[2]['error']

def test_callback_success(mock_channel, artifact_id):
    message = {"artifactId": artifact_id, "manifest": [], "title": "test", "footprint": "f" * 64}
    body = json.dumps(message).encode()
    method = MagicMock()
    method.delivery_tag = 1
    with patch("app.process_artifact_submission", return_value=True) as mock_process:
        app.callback(mock_channel, method, None, body)
        mock_process.assert_called_once_with(mock_channel, artifact_id, message)
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=1)

def test_callback_failure(mock_channel, artifact_id):
    message = {"artifactId": artifact_id, "manifest": [], "title": "test", "footprint": "f" * 64}
    body = json.dumps(message).encode()
    method = MagicMock()
    method.delivery_tag = 2
    with patch("app.process_artifact_submission", return_value=False) as mock_process:
        app.callback(mock_channel, method, None, body)
        mock_process.assert_called_once_with(mock_channel, artifact_id, message)
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

def test_health_check_handler_health():
    handler = app.HealthCheckHandler
    request = MagicMock()
    request.makefile.return_value = io.BytesIO()
    server = MagicMock()
    output = io.BytesIO()
    h = handler(request, ('127.0.0.1', 0), server)
    h.wfile = output
    h.path = '/health'
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h.do_GET()
    output.seek(0)
    assert b'healthy' in output.getvalue()

def test_health_check_handler_not_found():
    handler = app.HealthCheckHandler
    request = MagicMock()
    request.makefile.return_value = io.BytesIO()
    server = MagicMock()
    output = io.BytesIO()
    h = handler(request, ('127.0.0.1', 0), server)
    h.wfile = output
    h.path = '/bad'
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    h.do_GET()
    output.seek(0)
    assert b'Not Found' in output.getvalue()

def test_start_health_server_error(monkeypatch):
    class DummyServer:
        def __init__(self, *a, **kw):
            raise Exception('fail')
        def serve_forever(self):
            pass
    monkeypatch.setattr(app.http.server, 'HTTPServer', DummyServer)
    with patch.object(app.logger, 'error') as mock_log:
        try:
            app.start_health_server()
        except Exception:
            pass
        mock_log.assert_called()

def test_start_rabbitmq_consumer_connection_error(monkeypatch):
    class DummyAMQPError(Exception): pass
    monkeypatch.setattr(app.pika.exceptions, 'AMQPConnectionError', DummyAMQPError)
    monkeypatch.setattr(app.pika, 'BlockingConnection', lambda *a, **kw: (_ for _ in ()).throw(DummyAMQPError('fail')))
    with patch.object(app.logger, 'warning') as mock_warn, \
         patch.object(app.logger, 'error') as mock_err, \
         patch('app.time.sleep', return_value=None):
        app.start_rabbitmq_consumer()
        assert mock_warn.called or mock_err.called

def test_start_rabbitmq_consumer_success(monkeypatch):
    mock_conn = MagicMock()
    mock_chan = MagicMock()
    mock_chan.start_consuming.side_effect = lambda: None
    mock_conn.channel.return_value = mock_chan
    monkeypatch.setattr(app.pika, 'BlockingConnection', lambda *a, **kw: mock_conn)
    monkeypatch.setattr(app.pika, 'PlainCredentials', lambda u, p: None)
    monkeypatch.setattr(app.pika, 'ConnectionParameters', lambda **kw: None)
    monkeypatch.setattr(app.pika.exceptions, 'AMQPConnectionError', Exception)
    with patch.object(app.logger, 'info') as mock_info:
        app.start_rabbitmq_consumer()
        assert mock_info.called

def test_callback_unexpected_error(mock_channel):
    """Test callback handling of unexpected errors (e.g., malformed message structure)."""
    body = json.dumps("a string, not a dict").encode()
    method = MagicMock()
    method.delivery_tag = 5
    with patch.object(app.logger, 'error') as mock_log:
        app.callback(mock_channel, method, None, body)
        mock_channel.basic_nack.assert_called_once_with(delivery_tag=5, requeue=False)
        mock_log.assert_any_call("Unexpected error processing message: 'str' object has no attribute 'get'")

def test_publish_artifact_submitted_exception(mock_channel, artifact_id, submission_result_success):
    """Test exception handling during artifact submission publishing."""
    with patch("json.dumps", side_effect=TypeError("JSON serialization failed")):
        with pytest.raises(TypeError, match="JSON serialization failed"):
            app.publish_artifact_submitted(mock_channel, artifact_id, submission_result_success)

def test_start_rabbitmq_consumer_keyboard_interrupt(monkeypatch):
    """Test that KeyboardInterrupt stops the consumer."""
    mock_conn = MagicMock()
    mock_chan = MagicMock()
    # Simulate KeyboardInterrupt being raised when start_consuming is called
    mock_chan.start_consuming.side_effect = KeyboardInterrupt
    mock_conn.channel.return_value = mock_chan
    # Configure the mock to report that the connection is open
    mock_conn.is_closed = False
    monkeypatch.setattr(app.pika, 'BlockingConnection', lambda *a, **kw: mock_conn)
    
    with patch.object(app.logger, 'info') as mock_log:
        app.start_rabbitmq_consumer()
        mock_log.assert_any_call("Shutting down consumer...")
        mock_chan.stop_consuming.assert_called_once()
        mock_conn.close.assert_called_once()


# ── publish_artifact_updated ──────────────────────────────────────────────────

def test_publish_artifact_updated_success(mock_channel, artifact_id):
    update_result = {"success": True, "txId": "tx-upd-1"}
    app.publish_artifact_updated(mock_channel, artifact_id, update_result)
    args, kwargs = mock_channel.basic_publish.call_args
    body = json.loads(kwargs["body"])
    assert body["artifactId"] == artifact_id
    assert body["submissionState"] == "SUCCESS"
    assert body["blockchainTxId"] == "tx-upd-1"
    assert "error" not in body


def test_publish_artifact_updated_failure(mock_channel, artifact_id):
    update_result = {"success": False, "error": "update failed"}
    app.publish_artifact_updated(mock_channel, artifact_id, update_result)
    args, kwargs = mock_channel.basic_publish.call_args
    body = json.loads(kwargs["body"])
    assert body["submissionState"] == "FAILED"
    assert body["error"] == "update failed"
    assert "blockchainTxId" not in body


def test_publish_artifact_updated_uses_transaction_id_alias(mock_channel, artifact_id):
    """Should pick up 'transactionId' when 'txId' is absent."""
    update_result = {"success": True, "transactionId": "tx-alias-1"}
    app.publish_artifact_updated(mock_channel, artifact_id, update_result)
    args, kwargs = mock_channel.basic_publish.call_args
    body = json.loads(kwargs["body"])
    assert body["blockchainTxId"] == "tx-alias-1"


def test_publish_artifact_updated_raises_on_channel_error(mock_channel, artifact_id):
    mock_channel.basic_publish.side_effect = Exception("channel closed")
    with pytest.raises(Exception, match="channel closed"):
        app.publish_artifact_updated(mock_channel, artifact_id, {"success": True})


# ── process_artifact_update ───────────────────────────────────────────────────

def test_process_artifact_update_success(mock_channel, artifact_id):
    patch_data = {"footprint": "f" * 64, "keywords": ["kw1"]}
    with patch.object(app.peer_client, "update_artifact", return_value={"success": True, "txId": "tx-1"}) as mock_update:
        with patch("app.publish_artifact_updated") as mock_publish:
            result = app.process_artifact_update(mock_channel, artifact_id, patch_data)
            assert result is True
            mock_update.assert_called_once_with(artifact_id, patch_data)
            mock_publish.assert_called_once()


def test_process_artifact_update_with_nested_patch(mock_channel, artifact_id):
    """Should unwrap nested patch dict when message has {artifactId, patch:{...}}."""
    message = {
        "artifactId": artifact_id,
        "patch": {"footprint": "e" * 64, "keywords": ["kw2"]},
    }
    with patch.object(app.peer_client, "update_artifact", return_value={"success": True}) as mock_update:
        with patch("app.publish_artifact_updated"):
            result = app.process_artifact_update(mock_channel, artifact_id, message)
            assert result is True
            called_patch = mock_update.call_args[0][1]
            assert called_patch == {"footprint": "e" * 64, "keywords": ["kw2"]}


def test_process_artifact_update_invalid_footprint(mock_channel, artifact_id):
    patch_data = {"footprint": "not-a-valid-footprint"}
    with patch("app.publish_artifact_updated") as mock_publish:
        result = app.process_artifact_update(mock_channel, artifact_id, patch_data)
        assert result is False
        mock_publish.assert_called_once()
        args, _ = mock_publish.call_args
        assert args[2]["success"] is False


def test_process_artifact_update_no_footprint_succeeds(mock_channel, artifact_id):
    """When footprint is absent from patch, update should proceed without footprint validation."""
    patch_data = {"keywords": ["kw1"]}
    with patch.object(app.peer_client, "update_artifact", return_value={"success": True}) as mock_update:
        with patch("app.publish_artifact_updated"):
            result = app.process_artifact_update(mock_channel, artifact_id, patch_data)
            assert result is True


def test_process_artifact_update_peer_exception(mock_channel, artifact_id):
    patch_data = {"keywords": ["kw1"]}
    with patch.object(app.peer_client, "update_artifact", side_effect=Exception("bridge down")):
        with patch("app.publish_artifact_updated") as mock_publish:
            result = app.process_artifact_update(mock_channel, artifact_id, patch_data)
            assert result is False
            args, _ = mock_publish.call_args
            assert args[2]["success"] is False
            assert "bridge down" in args[2]["error"]


def test_process_artifact_update_non_dict_patch_uses_empty(mock_channel, artifact_id):
    """When patch_data is not a dict, effective_patch should fall back to empty dict."""
    with patch.object(app.peer_client, "update_artifact", return_value={"success": True}) as mock_update:
        with patch("app.publish_artifact_updated"):
            result = app.process_artifact_update(mock_channel, artifact_id, "not-a-dict")
            assert result is True
            called_patch = mock_update.call_args[0][1]
            assert called_patch == {}


# ── callback routing_key dispatching ─────────────────────────────────────────

def test_callback_routes_to_update_on_artifact_update_routing_key(mock_channel, artifact_id):
    """Messages with routing_key='artifact.update' should go to process_artifact_update."""
    message = {"artifactId": artifact_id, "patch": {}}
    body = json.dumps(message).encode()
    method = MagicMock()
    method.delivery_tag = 10
    method.routing_key = "artifact.update"
    with patch("app.process_artifact_update", return_value=True) as mock_update:
        app.callback(mock_channel, method, None, body)
        mock_update.assert_called_once_with(mock_channel, artifact_id, message)
        mock_channel.basic_ack.assert_called_once_with(delivery_tag=10)


def test_callback_routes_to_submit_on_non_update_routing_key(mock_channel, artifact_id):
    """Messages with a non-update routing_key should go to process_artifact_submission."""
    message = {"artifactId": artifact_id, "manifest": [], "title": "T", "footprint": "f" * 64}
    body = json.dumps(message).encode()
    method = MagicMock()
    method.delivery_tag = 11
    method.routing_key = "artifact.submit"
    with patch("app.process_artifact_submission", return_value=True) as mock_submit:
        app.callback(mock_channel, method, None, body)
        mock_submit.assert_called_once_with(mock_channel, artifact_id, message)


# ── process_artifact_submission invalid footprint ─────────────────────────────

def test_process_artifact_submission_invalid_footprint(mock_channel, artifact_id):
    """Submission with invalid footprint should publish FAILED and return False."""
    artifact_data = {"manifest": [], "title": "T", "footprint": "not64chars"}
    with patch("app.publish_artifact_submitted") as mock_publish:
        result = app.process_artifact_submission(mock_channel, artifact_id, artifact_data)
        assert result is False
        args, _ = mock_publish.call_args
        assert args[2]["success"] is False
        assert "footprint" in args[2]["error"].lower()


def test_process_artifact_submission_cleans_dois(mock_channel, artifact_id):
    """Submission should strip non-string/blank dois from the payload sent upstream."""
    artifact_data = {
        "manifest": [{"filename": "f.txt", "hash": "a" * 64}],
        "title": "T",
        "footprint": "f" * 64,
        "dois": ["10.1234/valid", "", None, "  "],
    }
    with patch.object(app.peer_client, "submit_artifact", return_value={"success": True}) as mock_sub:
        with patch("app.publish_artifact_submitted"):
            app.process_artifact_submission(mock_channel, artifact_id, artifact_data)
            submitted_payload = mock_sub.call_args[0][0]
            assert submitted_payload["data"]["dois"] == ["10.1234/valid"]