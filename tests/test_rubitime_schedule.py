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
    assert payload.end["dateTime"] == "2024-04-19T13:00:00+03:00"
    assert payload.extended_properties["private"][AVAILABLE_SLOT_MARKER_KEY] == AVAILABLE_SLOT_MARKER_VALUE


def test_cleans_existing_available_slot_events() -> None:
    service = RubitimeScheduleSyncService(_settings())

    class FakeCalendar:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def list_events(self, **_: object) -> list[dict[str, object]]:
            return [
                {"id": "1", "summary": AVAILABLE_SLOT_SUMMARY},
                {
                    "id": f"{AVAILABLE_SLOT_ID_PREFIX}202404191200",
                    "summary": "Something else",
                    "extendedProperties": {
                        "private": {AVAILABLE_SLOT_MARKER_KEY: AVAILABLE_SLOT_MARKER_VALUE}
                    },
                },
                {"id": "3", "summary": "Something else"},
                {"id": "4", "summary": "Busy"},
            ]

        def delete_event(self, event_id: str) -> dict[str, object]:
            self.deleted.append(event_id)
            return {"google_event_id": event_id, "outcome": "deleted"}

    calendar = FakeCalendar()

    deleted = service.delete_existing_slot_events(calendar)  # type: ignore[arg-type]

    # Должны быть найдены и удалены только события с подходящим префиксом ID
    assert deleted == 1
    assert calendar.deleted == [f"{AVAILABLE_SLOT_ID_PREFIX}202404191200"]
