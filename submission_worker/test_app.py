import pytest
from unittest.mock import patch, MagicMock
import app
import json
import io
import socket
import http.server
import threading
import time
import types

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

def test_health_check_handler_health():
    handler = app.HealthCheckHandler
    request = MagicMock()
    request.makefile.return_value = io.BytesIO()
    server = MagicMock()
    output = io.BytesIO()
    # Create an instance
    h = handler(request, ('127.0.0.1', 0), server)
    h.wfile = output
    h.path = '/health'
    h.send_response = MagicMock()
    h.send_header = MagicMock()
    h.end_headers = MagicMock()
    # Call the real do_GET
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
    # Patch HTTPServer to raise an error and serve_forever to not block
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
    # Patch pika.BlockingConnection to raise AMQPConnectionError
    class DummyAMQPError(Exception): pass
    monkeypatch.setattr(app.pika.exceptions, 'AMQPConnectionError', DummyAMQPError)
    monkeypatch.setattr(app.pika, 'BlockingConnection', lambda *a, **kw: (_ for _ in ()).throw(DummyAMQPError('fail')))
    with patch.object(app.logger, 'warning') as mock_warn, \
         patch.object(app.logger, 'error') as mock_err, \
         patch('app.time.sleep', return_value=None):
        app.start_rabbitmq_consumer()
        assert mock_warn.called or mock_err.called

def test_start_rabbitmq_consumer_success(monkeypatch):
    # Patch pika.BlockingConnection to succeed and channel.start_consuming to not block
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