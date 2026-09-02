import io
import json
from unittest.mock import MagicMock, patch

import pytest

import app

TOKEN_CORRELATION = "request-001"
ARTIFACT_ID = "00000000-0000-4000-8000-000000000001"
WORKFLOW_ID = "00000000-0000-4000-8000-000000000002"


def organization(msp_id="NSGMSP", organization_id="nsg"):
    return {
        "id": organization_id,
        "name": "nEUROSCIENCE GATEWAY",
        "mspId": msp_id,
    }


def metadata(operation, organization_id="nsg", correlation_id=TOKEN_CORRELATION):
    return {
        "authenticatedUserId": "user-001",
        "organizationId": organization_id,
        "correlationId": correlation_id,
        "operation": operation,
        "requestedAt": "2026-09-01T22:00:00Z",
    }


def artifact_command(operation="artifact.create"):
    command = {
        "contractVersion": "v3",
        "artifactId": ARTIFACT_ID,
        "organization": organization(),
        "correlationId": TOKEN_CORRELATION,
        "request": metadata(operation),
    }
    if operation == "artifact.create":
        command.update(
            {
                "title": "Reproducible artifact",
                "manifest": [],
                "footprint": "a" * 64,
                "dois": ["10.1234/example", "", None, "  "],
            }
        )
    else:
        command["patch"] = {"keywords": ["provenance"]}
    return command


def workflow_command(operation="workflow.create"):
    command = {
        "contractVersion": "v3",
        "workflowId": WORKFLOW_ID,
        "organization": organization(),
        "correlationId": TOKEN_CORRELATION,
        "request": metadata(operation),
    }
    if operation == "workflow.create":
        command.update({"title": "Workflow", "artifactIds": [ARTIFACT_ID]})
    else:
        command["patch"] = {"keywords": ["reproducible"]}
    return command


@pytest.fixture
def channel():
    mock = MagicMock()
    mock.basic_publish.return_value = True
    return mock


def delivery(routing_key, tag=1):
    method = MagicMock()
    method.routing_key = routing_key
    method.delivery_tag = tag
    return method


def properties(deaths=None):
    value = MagicMock()
    value.headers = {"x-death": deaths or []}
    return value


def test_completion_publication_is_persistent_confirmed_and_traceable(channel):
    app.publish_artifact_submitted(
        channel,
        ARTIFACT_ID,
        {"success": True, "txId": "tx-001", "peerId": "NSGMSP"},
        TOKEN_CORRELATION,
    )
    kwargs = channel.basic_publish.call_args.kwargs
    message = json.loads(kwargs["body"])
    assert kwargs["exchange"] == "artifact.exchange"
    assert kwargs["routing_key"] == "artifact.submitted"
    assert kwargs["mandatory"] is True
    assert kwargs["properties"].delivery_mode == 2
    assert kwargs["properties"].correlation_id == TOKEN_CORRELATION
    assert message["submissionState"] == "SUCCESS"
    assert message["blockchainTxId"] == "tx-001"


def test_failed_completion_is_bounded_and_contains_no_transaction(channel):
    app.publish_artifact_updated(
        channel,
        ARTIFACT_ID,
        {"success": False, "error": "x" * 700},
        TOKEN_CORRELATION,
    )
    message = json.loads(channel.basic_publish.call_args.kwargs["body"])
    assert message["submissionState"] == "FAILED"
    assert len(message["error"]) == 512
    assert "blockchainTxId" not in message


def test_missing_publisher_confirm_is_retryable(channel):
    channel.basic_publish.return_value = False
    with pytest.raises(app.RetryableProcessingError):
        app.publish_workflow_submitted(
            channel, WORKFLOW_ID, {"success": True}, TOKEN_CORRELATION
        )


def test_envelope_rejects_old_contract_and_cross_org_metadata():
    command = artifact_command()
    command["contractVersion"] = "v2"
    with pytest.raises(app.PermanentProcessingError, match="v3"):
        app._validate_envelope(command, "artifact.create")

    command = artifact_command()
    command["request"]["organizationId"] = "citizen-science"
    with pytest.raises(app.PermanentProcessingError, match="organization"):
        app._validate_envelope(command, "artifact.create")


def test_artifact_submission_routes_to_nsg_and_preserves_request(channel):
    command = artifact_command()
    with patch.object(
        app.peer_clients["NSGMSP"],
        "submit_artifact",
        return_value={"success": True, "txId": "tx-001"},
    ) as submit, patch("app.publish_artifact_submitted") as publish:
        assert app.process_artifact_submission(channel, ARTIFACT_ID, command) is True
    payload = submit.call_args.args[0]
    assert payload["request"] == command["request"]
    assert payload["organization"]["mspId"] == "NSGMSP"
    assert payload["dois"] == ["10.1234/example"]
    assert "data" not in payload
    publish.assert_called_once()


def test_artifact_submission_routes_to_citizen_science(channel):
    command = artifact_command()
    command["organization"] = organization(
        "CitizenScienceMSP", "citizen-science"
    )
    command["request"]["organizationId"] = "citizen-science"
    with patch.object(
        app.peer_clients["CitizenScienceMSP"],
        "submit_artifact",
        return_value={"success": True},
    ) as submit, patch("app.publish_artifact_submitted"):
        assert app.process_artifact_submission(channel, ARTIFACT_ID, command) is True
    submit.assert_called_once()


def test_transient_gateway_result_does_not_publish_terminal_event(channel):
    command = artifact_command()
    with patch.object(
        app.peer_clients["NSGMSP"],
        "submit_artifact",
        return_value={"success": False, "retryable": True, "error": "peer down"},
    ), patch("app.publish_artifact_submitted") as publish:
        with pytest.raises(app.RetryableProcessingError, match="peer down"):
            app.process_artifact_submission(channel, ARTIFACT_ID, command)
    publish.assert_not_called()


def test_permanent_gateway_result_publishes_failure(channel):
    command = artifact_command()
    with patch.object(
        app.peer_clients["NSGMSP"],
        "submit_artifact",
        return_value={"success": False, "retryable": False, "error": "invalid"},
    ), patch("app.publish_artifact_submitted") as publish:
        assert app.process_artifact_submission(channel, ARTIFACT_ID, command) is True
    assert publish.call_args.args[2]["success"] is False


def test_invalid_domain_payload_becomes_a_terminal_failure(channel):
    command = artifact_command()
    del command["manifest"]
    with patch("app.publish_artifact_submitted") as publish:
        assert app.process_artifact_submission(channel, ARTIFACT_ID, command) is True
    assert "manifest" in publish.call_args.args[2]["error"].lower()


def test_artifact_update_preserves_v3_metadata(channel):
    command = artifact_command("artifact.update")
    with patch.object(
        app.peer_clients["NSGMSP"],
        "update_artifact",
        return_value={"success": True},
    ) as update, patch("app.publish_artifact_updated"):
        assert app.process_artifact_update(channel, ARTIFACT_ID, command) is True
    update.assert_called_once_with(
        ARTIFACT_ID,
        {"keywords": ["provenance"]},
        organization=command["organization"],
        contract_version="v3",
        request=command["request"],
        correlation_id=TOKEN_CORRELATION,
    )


def test_workflow_create_and_update_use_dedicated_endpoints(channel):
    create = workflow_command()
    update = workflow_command("workflow.update")
    client = app.peer_clients["NSGMSP"]
    with patch.object(
        client, "submit_workflow", return_value={"success": True}
    ) as submit, patch.object(
        client, "update_workflow", return_value={"success": True}
    ) as update_call, patch("app.publish_workflow_submitted"), patch(
        "app.publish_workflow_updated"
    ):
        assert app.process_workflow_submission(channel, WORKFLOW_ID, create) is True
        assert app.process_workflow_update(channel, WORKFLOW_ID, update) is True
    assert submit.call_args.args[0]["artifactIds"] == [ARTIFACT_ID]
    assert update_call.call_args.kwargs["request"]["operation"] == "workflow.update"


def test_callback_acknowledges_success(channel):
    command = artifact_command()
    with patch("app.process_artifact_submission", return_value=True) as process:
        app.callback(
            channel,
            delivery("artifact.submit", 10),
            properties(),
            json.dumps(command).encode(),
        )
    process.assert_called_once_with(channel, ARTIFACT_ID, command)
    channel.basic_ack.assert_called_once_with(delivery_tag=10)


def test_callback_rejects_malformed_json_without_requeue(channel):
    app.callback(channel, delivery("artifact.submit", 11), properties(), b"not-json")
    channel.basic_nack.assert_called_once_with(delivery_tag=11, requeue=False)


def test_callback_records_permanent_validation_failure_then_acks(channel):
    command = artifact_command()
    command["request"]["operation"] = "artifact.update"
    with patch("app.publish_artifact_submitted") as publish:
        app.callback(
            channel,
            delivery("artifact.submit", 12),
            properties(),
            json.dumps(command).encode(),
        )
    publish.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=12)


def test_callback_dead_letters_a_transient_failure_for_retry(channel):
    command = artifact_command()
    with patch(
        "app.process_artifact_submission",
        side_effect=app.RetryableProcessingError("peer unavailable"),
    ):
        app.callback(
            channel,
            delivery("artifact.submit", 13),
            properties(),
            json.dumps(command).encode(),
        )
    channel.basic_nack.assert_called_once_with(delivery_tag=13, requeue=False)
    channel.basic_ack.assert_not_called()


def test_callback_emits_failure_after_bounded_retries(channel):
    command = artifact_command()
    deaths = [
        {"reason": "rejected", "queue": app.RABBITMQ_QUEUE_SUBMIT, "count": 3}
    ]
    with patch(
        "app.process_artifact_submission",
        side_effect=app.RetryableProcessingError("peer unavailable"),
    ), patch("app.publish_artifact_submitted") as publish:
        app.callback(
            channel,
            delivery("artifact.submit", 14),
            properties(deaths),
            json.dumps(command).encode(),
        )
    publish.assert_called_once()
    channel.basic_ack.assert_called_once_with(delivery_tag=14)


def test_topology_uses_dead_letter_retry_queues():
    channel = MagicMock()
    app.declare_messaging_topology(channel)
    declarations = channel.queue_declare.call_args_list
    main = next(
        call for call in declarations if call.kwargs["queue"] == "artifact.submit.queue"
    )
    retry = next(
        call
        for call in declarations
        if call.kwargs["queue"] == "artifact.submit.queue.retry"
    )
    assert main.kwargs["arguments"]["x-dead-letter-exchange"] == "osc.retry.exchange"
    assert retry.kwargs["arguments"]["x-message-ttl"] == app.RETRY_DELAY_MS
    assert retry.kwargs["arguments"]["x-dead-letter-exchange"] == "artifact.exchange"


def test_tls_is_mandatory_for_aws(monkeypatch):
    monkeypatch.setattr(app, "ENVIRONMENT", "aws")
    monkeypatch.setattr(app, "RABBITMQ_TLS", False)
    with pytest.raises(RuntimeError, match="TLS"):
        app._tls_options()


def test_tls_uses_server_name_and_tls_12(monkeypatch):
    context = MagicMock()
    monkeypatch.setattr(app, "RABBITMQ_TLS", True)
    monkeypatch.setattr(app.ssl, "create_default_context", lambda cafile=None: context)
    monkeypatch.setattr(app.pika, "SSLOptions", lambda value, host: (value, host))
    result = app._tls_options()
    assert result == (context, app.RABBITMQ_HOST)
    assert context.minimum_version == app.ssl.TLSVersion.TLSv1_2


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
    body = output.getvalue()
    assert b"submission-worker" in body
    assert b"LEDGER_GATEWAY_TOKEN" not in body


def test_consumer_enables_confirms_and_consumes_four_command_queues(monkeypatch):
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
