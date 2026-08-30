"""Rubitime schedule polling and Google Calendar synchronization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

from app.config import Settings
from app.models.google_event import GoogleEventPayload
from app.services.calendar import CalendarService
from app.utils.datetime import parse_start_datetime
from app.utils.logger import get_logger

log = get_logger(__name__)

RUBITIME_SCHEDULE_URL = "https://rubitime.ru/api2/get-schedule"
AVAILABLE_SLOT_SUMMARY = "Открыто для онлайн записи"
AVAILABLE_SLOT_DESCRIPTION = "Этот слот открыт для записи в Rubitime"
AVAILABLE_SLOT_COLOR_ID = "8"
AVAILABLE_SLOT_TRANSPARENCY = "transparent"
AVAILABLE_SLOT_STATUS = "tentative"
AVAILABLE_SLOT_ID_PREFIX = "available"
AVAILABLE_SLOT_MARKER_KEY = "rubitime_slot_type"
AVAILABLE_SLOT_MARKER_VALUE = "available"
AVAILABLE_SLOT_SOURCE_KEY = "rubitime_source"
AVAILABLE_SLOT_SOURCE_VALUE = "rubitime_schedule"


def _is_available(value: Any) -> bool:
    if value is True:
        return True
    if value in (1, "1"):
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on"}
    return False


@dataclass(frozen=True, slots=True)
class RubitimeSlot:
    start: datetime

    @property
    def end(self) -> datetime:
        return self.start + timedelta(hours=1)


class RubitimeScheduleSyncService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def is_configured(self) -> bool:
        reasons = []
        if not self._settings.google_calendar_id:
            reasons.append("google_calendar_id")
        if not self._settings.google_service_account_json:
            reasons.append("google_service_account_json")
        if not self._settings.rubitime_api_key:
            reasons.append("rubitime_api_key")
        if self._settings.rubitime_branch_id <= 0:
            reasons.append("rubitime_branch_id must be > 0")
        if self._settings.rubitime_cooperator_id <= 0:
            reasons.append("rubitime_cooperator_id must be > 0")
        if self._settings.rubitime_service_id <= 0:
            reasons.append("rubitime_service_id must be > 0")

        if reasons:
            log.info("rubitime_schedule_not_configured", missing_fields=reasons)
            return False
        log.info("rubitime_schedule_configured", service_enabled=True)
        return True

    def _request_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rk": self._settings.rubitime_api_key,
            "branch_id": self._settings.rubitime_branch_id,
            "cooperator_id": self._settings.rubitime_cooperator_id,
            "service_id": self._settings.rubitime_service_id,
        }
        if self._settings.rubitime_only_available:
            payload["only_available"] = 1
        return payload

    def fetch_schedule(self) -> dict[str, Any]:
        body = json.dumps(self._request_payload()).encode("utf-8")
        request = urllib_request.Request(
            RUBITIME_SCHEDULE_URL,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        log.info(
            "rubitime_schedule_request",
            url=RUBITIME_SCHEDULE_URL,
            branch_id=self._settings.rubitime_branch_id,
            cooperator_id=self._settings.rubitime_cooperator_id,
            service_id=self._settings.rubitime_service_id,
            only_available=self._settings.rubitime_only_available,
        )
        try:
            with urllib_request.urlopen(request, timeout=30) as response:
                raw = response.read().decode("utf-8")
        except urllib_error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            log.error(
                "rubitime_schedule_error",
                error_type="HTTPError",
                http_status=exc.code,
                reason=str(exc.reason),
                raw=raw,
            )
            raise
        except urllib_error.URLError as exc:
            log.error(
                "rubitime_schedule_error",
                error_type="URLError",
                reason=str(exc.reason),
            )
            raise

        if not raw.strip():
            raise ValueError("Empty response from Rubitime schedule API")

        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Rubitime schedule API returned non-object JSON")
        return parsed

    def parse_available_slots(self, payload: dict[str, Any]) -> list[RubitimeSlot]:
        if payload.get("status") != "ok":
            message = payload.get("message") or "Unknown Rubitime error"
            raise ValueError(f"Rubitime schedule API error: {message}")

        data = payload.get("data") or {}
        if not isinstance(data, dict):
            raise ValueError("Rubitime schedule API returned invalid data shape")

        slots: list[RubitimeSlot] = []
        date_count = 0
        slot_count = 0
        
        for date_value, times in data.items():
            if not isinstance(times, dict):
                continue
            date_count += 1
            for time_value, details in times.items():
                if not isinstance(details, dict) or not _is_available(details.get("available")):
                    continue
                start = parse_start_datetime(
                    f"{date_value} {time_value}",
                    self._settings.event_timezone,
                )
                slots.append(RubitimeSlot(start=start))
                slot_count += 1

        log.info(
            "rubitime_schedule_data_parsed",
            date_count=date_count,
            total_time_slots=slot_count,
            available_slots=len(slots),
        )
        
        slots.sort(key=lambda item: item.start)
        return slots

    def build_event(self, slot: RubitimeSlot) -> GoogleEventPayload:
        tz = self._settings.event_timezone
        # Генерировать ID с префиксом available (только a-v, цифры и подчеркивание для Google Calendar API)
        slot_id = f"available{slot.start.strftime('%Y%m%d%H%M')}"
        return GoogleEventPayload(
            event_id=slot_id,
            summary=AVAILABLE_SLOT_SUMMARY,
            description=AVAILABLE_SLOT_DESCRIPTION,
            location=self._settings.event_location,
            color_id=AVAILABLE_SLOT_COLOR_ID,
            transparency=AVAILABLE_SLOT_TRANSPARENCY,
            status=AVAILABLE_SLOT_STATUS,
            start={"dateTime": slot.start.isoformat(), "timeZone": tz},
            end={"dateTime": slot.end.isoformat(), "timeZone": tz},
            extended_properties={
                "private": {
                    AVAILABLE_SLOT_MARKER_KEY: AVAILABLE_SLOT_MARKER_VALUE,
                    AVAILABLE_SLOT_SOURCE_KEY: AVAILABLE_SLOT_SOURCE_VALUE,
                    "rubitime_slot_start": slot.start.isoformat(),
                    "rubitime_slot_end": slot.end.isoformat(),
                }
            },
        )

    def _slot_event_matches(self, event: dict[str, Any]) -> bool:
        event_id = str(event.get("id") or "").strip()
        # Искать по префиксу ID (новый способ)
        if event_id.startswith(AVAILABLE_SLOT_ID_PREFIX):
            return True
        return False

    def delete_existing_slot_events(self, calendar: CalendarService) -> int:
        # Искать события за последние 7 дней и на 7 дней вперед
        time_min = datetime.now(timezone.utc) - timedelta(days=7)
        time_max = datetime.now(timezone.utc) + timedelta(days=7)
        events = calendar.list_events(time_min=time_min, time_max=time_max)
        log.info("rubitime_schedule_cleanup_started", event_count=len(events))
        
        deleted = 0
        for event in events:
            if not self._slot_event_matches(event):
                continue
            event_id = event.get("id")
            if not event_id:
                continue
            try:
                calendar.delete_event(str(event_id))
                deleted += 1
                log.debug(
                    "rubitime_slot_deleted",
                    event_id=event_id,
                    summary=event.get("summary"),
                    start=event.get("start", {}).get("dateTime"),
                )
            except Exception as exc:
                log.error(
                    "rubitime_slot_deletion_failed",
                    event_id=event_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        log.info("rubitime_schedule_cleanup_finished", deleted_count=deleted)
        return deleted

    def sync_once(self) -> dict[str, Any]:
        if not self.is_configured():
            log.info("rubitime_schedule_sync_skipped", reason="missing_configuration")
            return {"status": "skipped", "reason": "missing_configuration"}

        log.info("rubitime_schedule_sync_started")
        calendar = CalendarService(self._settings)
        
        # Fetch schedule
        response = self.fetch_schedule()
        log.info("rubitime_schedule_response_received", status=response.get("status"), message=response.get("message"))
        
        # Parse available slots
        slots = self.parse_available_slots(response)
        log.info("rubitime_schedule_slots_parsed", count=len(slots))
        
        # Delete existing slot events
        deleted = self.delete_existing_slot_events(calendar)
        
        # Create new slot events
        created = 0
        for slot in slots:
            try:
                event = self.build_event(slot)
                calendar.insert_event(event.event_id, event)
                created += 1
                log.debug(
                    "rubitime_slot_created",
                    start=slot.start.isoformat(),
                    end=slot.end.isoformat(),
                )
            except Exception as exc:
                log.error(
                    "rubitime_slot_creation_failed",
                    start=slot.start.isoformat(),
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
        
        log.info(
            "rubitime_schedule_sync_finished",
            available_slots=len(slots),
            deleted_events=deleted,
            created_events=created,
        )
        return {
            "status": "ok",
            "available_slots": len(slots),
            "deleted_events": deleted,
            "created_events": created,
        }
