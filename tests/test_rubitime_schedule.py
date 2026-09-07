from __future__ import annotations

from app.config import Settings
from app.services.rubitime_schedule import (
    AVAILABLE_SLOT_COLOR_ID,
    AVAILABLE_SLOT_DESCRIPTION,
    AVAILABLE_SLOT_ID_PREFIX,
    AVAILABLE_SLOT_MARKER_KEY,
    AVAILABLE_SLOT_MARKER_VALUE,
    AVAILABLE_SLOT_STATUS,
    AVAILABLE_SLOT_SUMMARY,
    AVAILABLE_SLOT_TRANSPARENCY,
    RubitimeScheduleSyncService,
)


def _settings() -> Settings:
    return Settings(
        google_calendar_id="calendar@example.com",
        google_service_account_json="{}",
        google_event_color_id="5",
        google_include_attendees=False,
        rubitime_api_key="token",
        rubitime_branch_id=1,
        rubitime_cooperator_id=2,
        rubitime_service_id=3,
        rubitime_only_available=True,
        rubitime_sync_interval_seconds=300,
        event_timezone="Europe/Moscow",
        event_location="Москва, улица Мещерякова, 8, офис 5",
        log_level="INFO",
        app_host="0.0.0.0",
        app_port=8000,
    )


def test_parses_available_slots_from_rubitime_payload() -> None:
    service = RubitimeScheduleSyncService(_settings())
    payload = {
        "status": "ok",
        "message": "Success",
        "data": {
            "2024-04-19": {
                "11:00": {"available": False},
                "12:00": {"available": True},
                "13:00": {"available": True},
            }
        },
    }

    slots = service.parse_available_slots(payload)

    assert [slot.start.isoformat() for slot in slots] == [
        "2024-04-19T12:00:00+03:00",
        "2024-04-19T13:00:00+03:00",
    ]


def test_builds_google_event_for_available_slot() -> None:
    service = RubitimeScheduleSyncService(_settings())
    slot = service.parse_available_slots(
        {
            "status": "ok",
            "data": {"2024-04-19": {"12:00": {"available": True}}},
        }
    )[0]

    payload = service.build_event(slot)

    # Проверить формат ID (должен начинаться с available_)
    assert payload.event_id.startswith(AVAILABLE_SLOT_ID_PREFIX)
    assert payload.event_id == "available202404191200"
    
    assert payload.summary == AVAILABLE_SLOT_SUMMARY
    assert payload.description == AVAILABLE_SLOT_DESCRIPTION
    assert payload.color_id == AVAILABLE_SLOT_COLOR_ID
    assert payload.transparency == AVAILABLE_SLOT_TRANSPARENCY
    assert payload.status == AVAILABLE_SLOT_STATUS
    body = payload.to_calendar_body()
    assert body["transparency"] == AVAILABLE_SLOT_TRANSPARENCY
    assert body["status"] == AVAILABLE_SLOT_STATUS
    assert payload.start["dateTime"] == "2024-04-19T12:00:00+03:00"
    assert payload.end["dateTime"] == "2024-04-19T12:30:00+03:00"
    assert payload.extended_properties["private"][AVAILABLE_SLOT_MARKER_KEY] == AVAILABLE_SLOT_MARKER_VALUE


def test_groups_consecutive_thirty_minute_slots_into_windows() -> None:
    service = RubitimeScheduleSyncService(_settings())
    payload = {
        "status": "ok",
        "data": {
            "2026-09-19": {
                "11:00": {"available": True},
                "11:30": {"available": True},
                "12:00": {"available": True},
                "12:30": {"available": True},
                "13:00": {"available": True},
                "13:30": {"available": True},
                "14:00": {"available": True},
                "16:00": {"available": True},
                "16:30": {"available": True},
            },
            "2026-09-20": {
                "13:00": {"available": True},
                "13:30": {"available": True},
                "14:00": {"available": True},
                "14:30": {"available": True},
                "15:00": {"available": True},
                "15:30": {"available": True},
            },
        },
    }

    slots = service.parse_available_slots(payload)

    assert [(slot.start.isoformat(), slot.end.isoformat()) for slot in slots] == [
        ("2026-09-19T11:00:00+03:00", "2026-09-19T14:30:00+03:00"),
        ("2026-09-19T16:00:00+03:00", "2026-09-19T17:00:00+03:00"),
        ("2026-09-20T13:00:00+03:00", "2026-09-20T16:00:00+03:00"),
    ]


def test_reconcile_does_not_write_unchanged_slots() -> None:
    service = RubitimeScheduleSyncService(_settings())
    slot = service.parse_available_slots(
        {"status": "ok", "data": {"2024-04-19": {"12:00": {"available": True}}}}
    )[0]
    existing = service.build_event(slot).to_calendar_body()
    existing["id"] = "available202404191200"

    class FakeCalendar:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.inserted: list[str] = []
            self.updated: list[str] = []

        def list_events(self, **_: object) -> list[dict[str, object]]:
            return [existing]

        def insert_event(self, event_id: str, _: object) -> dict[str, object]:
            self.inserted.append(event_id)
            return {"id": event_id}

        def update_event(self, event_id: str, _: object) -> dict[str, object]:
            self.updated.append(event_id)
            return {"id": event_id}

        def delete_event(self, event_id: str) -> dict[str, object]:
            self.deleted.append(event_id)
            return {"google_event_id": event_id, "outcome": "deleted"}

    calendar = FakeCalendar()
    result = service.reconcile_slot_events(calendar, [slot])  # type: ignore[arg-type]

    assert result == {
        "created_events": 0,
        "updated_events": 0,
        "unchanged_events": 1,
        "deleted_events": 0,
        "failed_events": 0,
    }
    assert calendar.inserted == []
    assert calendar.updated == []
    assert calendar.deleted == []


def test_reconcile_creates_updates_and_deletes_only_diff() -> None:
    service = RubitimeScheduleSyncService(_settings())
    slots = service.parse_available_slots(
        {
            "status": "ok",
            "data": {
                "2024-04-19": {
                    "12:00": {"available": True},
                    "13:00": {"available": True},
                }
            },
        }
    )

    changed = service.build_event(slots[0]).to_calendar_body()
    changed["id"] = "available202404191200"
    changed["location"] = "Old location"
    stale = service.build_event(slots[0]).to_calendar_body()
    stale["id"] = "available202404181200"

    class FakeCalendar:
        def __init__(self) -> None:
            self.deleted: list[str] = []
            self.inserted: list[str] = []
            self.updated: list[str] = []
            self.list_kwargs: dict[str, object] = {}

        def list_events(self, **kwargs: object) -> list[dict[str, object]]:
            self.list_kwargs = kwargs
            return [changed, stale]

        def insert_event(self, event_id: str, _: object) -> dict[str, object]:
            self.inserted.append(event_id)
            return {"id": event_id}

        def update_event(self, event_id: str, _: object) -> dict[str, object]:
            self.updated.append(event_id)
            return {"id": event_id}

        def delete_event(self, event_id: str) -> dict[str, object]:
            self.deleted.append(event_id)
            return {"google_event_id": event_id, "outcome": "deleted"}

    calendar = FakeCalendar()
    result = service.reconcile_slot_events(calendar, slots)  # type: ignore[arg-type]

    assert result == {
        "created_events": 1,
        "updated_events": 1,
        "unchanged_events": 0,
        "deleted_events": 1,
        "failed_events": 0,
    }
    assert calendar.inserted == ["available202404191300"]
    assert calendar.updated == ["available202404191200"]
    assert calendar.deleted == ["available202404181200"]
    assert calendar.list_kwargs["private_extended_property"] == (
        "rubitime_source=rubitime_schedule"
    )
