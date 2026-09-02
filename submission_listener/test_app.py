import io
import json
from unittest.mock import MagicMock, patch

import pytest
import requests

import app

ARTIFACT_ID = "00000000-0000-4000-8000-000000000001"
WORKFLOW_ID = "00000000-0000-4000-8000-000000000002"
CORRELATION_ID = "request-001"


def artifact_event(kind="submitted", state="SUCCESS"):
    event = {
        "artifactId": ARTIFACT_ID,
        "submissionState": state,
        "version": "v1",
        "submittedAt" if kind == "submitted" else "updatedAt": "2026-09-01T22:00:00Z",
    }
    if state == "SUCCESS":
        event["blockchainTxId"] = "tx-001"
    else:
        event["error"] = "ledger rejected command"
    return event


def workflow_event(kind="submitted", state="SUCCESS"):
    event = {
        "workflowId": WORKFLOW_ID,
        "submissionState": state,
        "version": "v1",
        "submittedAt" if kind == "submitted" else "updatedAt": "2026-09-01T22:00:00Z",
    }
    if state == "FAILED":
        event["error"] = "ledger rejected command"
    return event


def delivery(routing_key, tag=1):
    method = MagicMock()
    method.routing_key = routing_key
    method.delivery_tag = tag
    return method


def properties(deaths=None):
    value = MagicMock()
    value.headers = {"x-death": deaths or []}
    value.correlation_id = CORRELATION_ID
    return value


@pytest.fixture
def channel():
    value = MagicMock()
    value.basic_publish.return_value = True
    return value


def test_artifact_schemas_accept_success_and_bounded_failure():
    assert app.validate_message(artifact_event(), app.artifact_submitted_schema)
    assert app.validate_message(
        artifact_event("updated", "FAILED"), app.artifact_updated_schema
    )
    invalid = artifact_event(state="FAILED")
    del invalid["error"]
    assert not app.validate_message(invalid, app.artifact_submitted_schema)


def test_workflow_validation_requires_failure_reason():
    assert app._validate_workflow_message(workflow_event(), "submitted")
    invalid = workflow_event(state="FAILED")
    del invalid["error"]
    assert not app._validate_workflow_message(invalid, "submitted")


def test_status_patch_maps_ledger_fields_without_transport_metadata():
    event = artifact_event("updated", "FAILED")
    event["peerId"] = "NSGMSP"
    patch_data = app._status_patch(event)
    assert patch_data == {
        "submissionState": "FAILED",
        "peerId": "NSGMSP",
        "updatedAt": "2026-09-01T22:00:00Z",
        "submissionError": "ledger rejected command",
    }


def test_artifact_update_sends_service_auth_and_correlation(monkeypatch):
    monkeypatch.setattr(app, "SUBMISSION_LISTENER_API_KEY", "internal-api-key")
    response = MagicMock(status_code=200)
    with patch.object(app.requests, "patch", return_value=response) as request:
        assert app.update_artifact_status(
            ARTIFACT_ID, artifact_event(), CORRELATION_ID
        )
    assert request.call_args.args[0].endswith(ARTIFACT_ID)
    assert request.call_args.kwargs["headers"]["X-API-Key"] == "internal-api-key"
    assert request.call_args.kwargs["headers"]["X-Correlation-ID"] == CORRELATION_ID
    assert request.call_args.kwargs["timeout"] == 10


def test_workflow_update_uses_workflow_endpoint():
    response = MagicMock(status_code=200)
    with patch.object(app.requests, "patch", return_value=response) as request:
        app.update_workflow_status(WORKFLOW_ID, workflow_event(), CORRELATION_ID)
    assert "/workflows/" in request.call_args.args[0]


@pytest.mark.parametrize("status", [408, 425, 429, 500, 503])
def test_transient_http_statuses_are_retryable(status):
    with patch.object(app.requests, "patch", return_value=MagicMock(status_code=status)):
        with pytest.raises(app.RetryableStatusUpdateError):
            app.update_artifact_status(ARTIFACT_ID, artifact_event())


def test_timeout_and_connection_failures_are_retryable():
    for error in (requests.Timeout(), requests.ConnectionError()):
        with patch.object(app.requests, "patch", side_effect=error):
            with pytest.raises(app.RetryableStatusUpdateError):
                app.update_artifact_status(ARTIFACT_ID, artifact_event())


def test_nonretryable_4xx_is_permanent():
    with patch.object(app.requests, "patch", return_value=MagicMock(status_code=403)):
        with pytest.raises(app.PermanentStatusUpdateError):
            app.update_artifact_status(ARTIFACT_ID, artifact_event())


def test_callback_updates_artifact_and_acks(channel):
    event = artifact_event()
    with patch("app.update_artifact_status", return_value=True) as update:
        app.callback(
            channel,
            delivery("artifact.submitted", 10),
            properties(),
            json.dumps(event).encode(),
        )
    update.assert_called_once_with(ARTIFACT_ID, event, CORRELATION_ID)
    channel.basic_ack.assert_called_once_with(delivery_tag=10)


def test_callback_routes_workflow_update(channel):
    event = workflow_event("updated")
    with patch("app.update_workflow_status", return_value=True) as update:
        app.callback(
            channel,
            delivery("workflow.updated", 11),
            properties(),
            json.dumps(event).encode(),
        )
    update.assert_called_once_with(WORKFLOW_ID, event, CORRELATION_ID)


def test_transient_api_failure_is_dead_lettered_for_retry(channel):
    with patch(
        "app.update_artifact_status",
        side_effect=app.RetryableStatusUpdateError("gateway unavailable"),
    ):
        app.callback(
            channel,
            delivery("artifact.submitted", 12),
            properties(),
            json.dumps(artifact_event()).encode(),
        )
    channel.basic_nack.assert_called_once_with(delivery_tag=12, requeue=False)
    channel.basic_ack.assert_not_called()


def test_exhausted_retry_is_parked_and_acknowledged(channel):
    deaths = [{"reason": "rejected", "count": 3}]
    with patch(
        "app.update_artifact_status",
        side_effect=app.RetryableStatusUpdateError("gateway unavailable"),
    ):
        app.callback(
            channel,
            delivery("artifact.submitted", 13),
            properties(deaths),
            json.dumps(artifact_event()).encode(),
        )
    publish = channel.basic_publish.call_args.kwargs
    assert publish["exchange"] == "osc.failed.exchange"
    assert publish["routing_key"] == "status.failed"
    assert publish["properties"].headers["original-routing-key"] == "artifact.submitted"
    channel.basic_ack.assert_called_once_with(delivery_tag=13)


def test_invalid_message_is_parked_immediately(channel):
    invalid = artifact_event()
    del invalid["artifactId"]
    app.callback(
        channel,
        delivery("artifact.submitted", 14),
        properties(),
        json.dumps(invalid).encode(),
    )
    channel.basic_publish.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=14)


def test_malformed_json_retries_then_parks(channel):
    app.callback(
        channel,
        delivery("artifact.submitted", 15),
        properties(),
        b"not-json",
    )
    channel.basic_nack.assert_called_once_with(delivery_tag=15, requeue=False)

    channel.reset_mock()
    app.callback(
        channel,
        delivery("artifact.submitted", 16),
        properties([{"reason": "rejected", "count": 3}]),
        b"not-json",
    )
    channel.basic_publish.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=16)


def test_failed_message_publication_requires_confirm(channel):
    channel.basic_publish.return_value = False
    with pytest.raises(app.RetryableStatusUpdateError, match="confirm"):
        app._park_message(
            channel,
            "artifact.submitted",
            b"{}",
            "failure",
            CORRELATION_ID,
        )


def test_topology_has_completion_retries_and_parking_queue():
    channel = MagicMock()
    app.declare_messaging_topology(channel)
    declarations = channel.queue_declare.call_args_list
    main = next(
        call
        for call in declarations
        if call.kwargs["queue"] == "artifact.submitted.queue"
    )
    retry = next(
        call
        for call in declarations
        if call.kwargs["queue"] == "artifact.submitted.queue.retry"
    )
    assert main.kwargs["arguments"]["x-dead-letter-exchange"] == "osc.listener.retry.exchange"
    assert retry.kwargs["arguments"]["x-message-ttl"] == app.RETRY_DELAY_MS
    assert any(
        call.kwargs["queue"] == "osc.status.failed.queue" for call in declarations
    )


def test_tls_is_required_in_aws(monkeypatch):
    monkeypatch.setattr(app, "ENVIRONMENT", "aws")
    monkeypatch.setattr(app, "RABBITMQ_TLS", False)
    with pytest.raises(RuntimeError, match="TLS"):
        app._tls_options()


def test_tls_uses_tls_12_and_server_name(monkeypatch):
    context = MagicMock()
    monkeypatch.setattr(app, "RABBITMQ_TLS", True)
    monkeypatch.setattr(app.ssl, "create_default_context", lambda cafile=None: context)
    monkeypatch.setattr(app.pika, "SSLOptions", lambda value, host: (value, host))
    assert app._tls_options() == (context, app.RABBITMQ_HOST)
    assert context.minimum_version == app.ssl.TLSVersion.TLSv1_2


def test_runtime_requires_nondefault_internal_api_key(monkeypatch):
    monkeypatch.setattr(app, "ENVIRONMENT", "production")
    monkeypatch.setattr(app, "SUBMISSION_LISTENER_API_KEY", "short")
    with pytest.raises(RuntimeError, match="API_KEY"):
        app._validate_runtime_configuration()


def test_consumer_uses_confirms_and_all_completion_queues(monkeypatch):
    connection = MagicMock()
    channel = MagicMock()
    connection.channel.return_value = channel
    connection.is_closed = False
    monkeypatch.setattr(app, "ENVIRONMENT", "local")
    monkeypatch.setattr(app.pika, "BlockingConnection", lambda _parameters: connection)
    monkeypatch.setattr(app.pika, "ConnectionParameters", lambda **kwargs: kwargs)
    monkeypatch.setattr(app.pika, "PlainCredentials", lambda *_args: None)
    monkeypatch.setattr(app, "declare_messaging_topology", MagicMock())
    app.start_rabbitmq_consumer()
    channel.confirm_delivery.assert_called_once()
    assert channel.basic_consume.call_count == 4
    connection.close.assert_called_once()


def test_health_endpoint_is_sanitized():
    handler = app.HealthCheckHandler
    request = MagicMock()
    request.makefile.return_value = io.BytesIO()
    output = io.BytesIO()
    instance = handler(request, ("127.0.0.1", 0), MagicMock())
    instance.wfile = output
    instance.path = "/health"
    instance.send_response = MagicMock()
    instance.send_header = MagicMock()
    instance.end_headers = MagicMock()
    instance.do_GET()
    assert b"submission-listener" in output.getvalue()
    assert b"apiKey" not in output.getvalue()
