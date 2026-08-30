from __future__ import annotations

import base64
import json

import webhook_handler
from app.routes import webhook as webhook_route


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


def test_process_webhook_deletes_auto_cancelled_updates(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeCalendar:
        def __init__(self, settings: object) -> None:
            calls["calendar_settings"] = settings

        def delete_event(self, event_id: str) -> dict[str, object]:
            calls["deleted_event_id"] = event_id
            return {"google_event_id": event_id, "outcome": "deleted"}

        def update_event(self, event_id: str, payload: object) -> dict[str, object]:
            calls["unexpected_update"] = (event_id, payload)
            return {"google_event_id": event_id, "outcome": "updated"}

        def insert_event(self, event_id: str, payload: object) -> dict[str, object]:
            calls["unexpected_insert"] = (event_id, payload)
            return {"google_event_id": event_id, "outcome": "inserted"}

    class FakeFormatter:
        def __init__(self, settings: object) -> None:
            calls["formatter_settings"] = settings

        def build(self, record_id: int, data: dict[str, object], event: str) -> object:
            calls["unexpected_build"] = (record_id, data, event)
            return object()

    monkeypatch.setattr(webhook_route, "CalendarService", FakeCalendar)
    monkeypatch.setattr(webhook_route, "EventFormatter", FakeFormatter)

    webhook_route.process_webhook(
        "event-update-record",
        8986532,
        {
            "id": 8986532,
            "parent_record": None,
            "status": 4,
            "name": "ИВАН ИВАНОВ ИВАНОВИЧ",
            "record": "2026-09-06 11:00:00",
        },
    )

    assert calls["deleted_event_id"] == "booked8986532"
    assert "unexpected_build" not in calls
    assert "unexpected_update" not in calls
    assert "unexpected_insert" not in calls
