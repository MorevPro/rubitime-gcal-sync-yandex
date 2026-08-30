"""End-to-end check against the real Google Calendar API.

Creates a throwaway event via CalendarService, reads it back through the
Calendar API to confirm what actually landed there (not just what the
formatter produced locally), then deletes it. Requires real credentials in
.env (GOOGLE_CALENDAR_ID + GOOGLE_SERVICE_ACCOUNT_JSON/_FILE); skipped
otherwise so CI without secrets still passes.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.models.google_event import GoogleEventPayload
from app.services.calendar import CalendarService
from app.services.formatter import EventFormatter

TEST_RECORD_ID = 9199999901

WEBHOOK_DATA = {
    "id": str(TEST_RECORD_ID),
    "parent_record": None,
    "record": "2026-09-01 12:00:00",
    "name": "АВТОТЕСТ ФОРМАТТЕРА",
    "phone": "+70000000000",
    "email": "autotest@example.com",
    "price": "1000",
    "status": "3",
    "duration": "50",
    "service_title": "Автотест",
    "branch_title": "Автотест филиал",
    "comment": "Создано автотестом, должно быть удалено",
    "custom_field1": "26.10.1985",
    "custom_field2": "М",
    "custom_field3": "01.01.1988",
    "custom_field4": "Имя ребенка ",
}


def _settings_configured() -> bool:
    settings = get_settings()
    return bool(settings.google_calendar_id and settings.google_service_account_json)


requires_real_calendar = pytest.mark.skipif(
    not _settings_configured(),
    reason="GOOGLE_CALENDAR_ID / GOOGLE_SERVICE_ACCOUNT_JSON not configured in .env",
)


@requires_real_calendar
def test_child_fields_survive_a_real_round_trip_through_google_calendar() -> None:
    settings = get_settings()
    calendar = CalendarService(settings)
    formatter = EventFormatter(settings)

    payload = formatter.build(TEST_RECORD_ID, dict(WEBHOOK_DATA), "event-create-record")
    event_id = GoogleEventPayload.google_event_id(TEST_RECORD_ID, "event-create-record")

    calendar.insert_event(event_id, payload)
    try:
        remote_event = calendar.get_event(event_id)
        description = remote_event.get("description", "")

        assert "Дата рождения ребенка: 01.01.1988" in description
        assert "Имя ребенка: Имя ребенка" in description

        lines = description.splitlines()
        assert lines.index("Дата рождения ребенка: 01.01.1988") < lines.index("Пол: М")
    finally:
        calendar.delete_event(event_id)

    # Google Calendar soft-deletes: GET still returns a tombstone with
    # status=cancelled instead of a 404 (see tests/test_google_calendar.py notes).
    tombstone = calendar.get_event(event_id)
    assert tombstone.get("status") == "cancelled"
