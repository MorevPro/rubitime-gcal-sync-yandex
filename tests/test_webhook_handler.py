from __future__ import annotations

import base64
import json

import webhook_handler


def _event(body: str, is_base64: bool = False, method: str = "POST") -> dict:
    return {
        "httpMethod": method,
        "headers": {"Content-Type": "application/json"},
        "body": body,
        "isBase64Encoded": is_base64,
    }


def test_health_check_on_get() -> None:
    response = webhook_handler.handler(_event("", method="GET"), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"])["ok"] is True


def test_webhook_returns_400_on_empty_body() -> None:
    response = webhook_handler.handler(_event(""), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"ok": False, "error": "empty_or_invalid_body"}


def test_webhook_decodes_base64_body() -> None:
    raw = json.dumps(
        {
            "event": "event-remove-record",
            "data": {"id": 42, "parent_record": None},
        }
    )
    encoded = base64.b64encode(raw.encode("utf-8")).decode("ascii")

    response = webhook_handler.handler(_event(encoded, is_base64=True), None)

    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["record_id"] == 42
    assert body["event"] == "event-remove-record"


def test_webhook_processes_and_returns_ok(monkeypatch) -> None:
    processed: dict[str, object] = {}

    def fake_process_webhook(event: str, record_id: int, data: dict[str, object]) -> None:
        processed["event"] = event
        processed["record_id"] = record_id
        processed["data"] = data

    monkeypatch.setattr(webhook_handler, "process_webhook", fake_process_webhook)

    body = json.dumps(
        {
            "from": "user",
            "event": "event-create-record",
            "data": {
                "id": 8522929,
                "parent_record": None,
                "name": "Ivan",
                "record": "2026-06-28 15:00:00",
            },
        }
    )

    response = webhook_handler.handler(_event(body), None)

    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {
        "ok": True,
        "accepted": True,
        "record_id": 8522929,
        "event": "event-create-record",
    }
    assert processed["event"] == "event-create-record"
    assert processed["record_id"] == 8522929


def test_webhook_returns_400_on_validation_error() -> None:
    body = json.dumps({"event": "unknown-event", "data": {"id": 1, "parent_record": None}})

    response = webhook_handler.handler(_event(body), None)

    assert response["statusCode"] == 400
    assert json.loads(response["body"]) == {"ok": False, "error": "validation_failed"}
