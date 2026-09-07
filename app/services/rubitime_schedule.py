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
RUBITIME_SERVICE_DURATION = timedelta(minutes=30)


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
    end: datetime


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
                slots.append(
                    RubitimeSlot(
                        start=start,
                        end=start + RUBITIME_SERVICE_DURATION,
                    )
                )
                slot_count += 1

        log.info(
            "rubitime_schedule_data_parsed",
            date_count=date_count,
            total_time_slots=slot_count,
            available_time_slots=len(slots),
        )

        slots.sort(key=lambda item: item.start)
        grouped_slots: list[RubitimeSlot] = []
        for slot in slots:
            if grouped_slots and slot.start == grouped_slots[-1].end:
                grouped_slots[-1] = RubitimeSlot(
                    start=grouped_slots[-1].start,
                    end=slot.end,
                )
            else:
                grouped_slots.append(slot)

        log.info(
            "rubitime_schedule_slots_grouped",
            source_slot_count=len(slots),
            grouped_slot_count=len(grouped_slots),
            service_duration_minutes=int(
                RUBITIME_SERVICE_DURATION.total_seconds() // 60
            ),
        )
        return grouped_slots

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
        private = (event.get("extendedProperties") or {}).get("private") or {}
        if (
            private.get(AVAILABLE_SLOT_MARKER_KEY) == AVAILABLE_SLOT_MARKER_VALUE
            and private.get(AVAILABLE_SLOT_SOURCE_KEY) == AVAILABLE_SLOT_SOURCE_VALUE
        ):
            return True

        event_id = str(event.get("id") or "").strip()
        # Обратная совместимость со слотами, созданными до добавления marker.
        if event_id.startswith(AVAILABLE_SLOT_ID_PREFIX):
            return True
        return False

    def list_existing_slot_events(self, calendar: CalendarService) -> list[dict[str, Any]]:
        """Return only events managed by the Rubitime schedule sync."""
        time_min = datetime.now(timezone.utc) - timedelta(days=7)
        events = calendar.list_events(
            time_min=time_min,
            private_extended_property=(
                f"{AVAILABLE_SLOT_SOURCE_KEY}={AVAILABLE_SLOT_SOURCE_VALUE}"
            ),
        )
        managed_events = [event for event in events if self._slot_event_matches(event)]
        log.info(
            "rubitime_schedule_existing_events_loaded",
            event_count=len(managed_events),
        )
        return managed_events

    @staticmethod
    def _event_matches_payload(
        existing: dict[str, Any],
        desired: GoogleEventPayload,
    ) -> bool:
        """Compare fields owned by this integration, ignoring Google metadata."""
        body = desired.to_calendar_body()
        scalar_fields = (
            "summary",
            "description",
            "location",
            "colorId",
            "transparency",
            "status",
        )
        if any(existing.get(field) != body.get(field) for field in scalar_fields):
            return False

        for boundary in ("start", "end"):
            if (existing.get(boundary) or {}).get("dateTime") != (
                body.get(boundary) or {}
            ).get("dateTime"):
                return False

        existing_private = (
            (existing.get("extendedProperties") or {}).get("private") or {}
        )
        desired_private = (
            (body.get("extendedProperties") or {}).get("private") or {}
        )
        return all(
            existing_private.get(key) == value
            for key, value in desired_private.items()
        )

    def reconcile_slot_events(
        self,
        calendar: CalendarService,
        slots: list[RubitimeSlot],
    ) -> dict[str, int]:
        """Apply the in-memory diff between Rubitime and Google Calendar."""
        desired_by_id = {
            event.event_id: event
            for event in (self.build_event(slot) for slot in slots)
            if event.event_id
        }
        existing_by_id = {
            str(event["id"]): event
            for event in self.list_existing_slot_events(calendar)
            if event.get("id")
        }

        created = 0
        updated = 0
        unchanged = 0
        deleted = 0
        failed = 0

        # First make the desired state available, then remove stale events.
        for event_id, desired in desired_by_id.items():
            existing = existing_by_id.get(event_id)
            if existing is not None and self._event_matches_payload(existing, desired):
                unchanged += 1
                continue
            try:
                if existing is None:
                    calendar.insert_event(event_id, desired)
                    created += 1
                    operation = "created"
                else:
                    calendar.update_event(event_id, desired)
                    updated += 1
                    operation = "updated"
                log.debug(
                    f"rubitime_slot_{operation}",
                    event_id=event_id,
                    start=desired.start.get("dateTime"),
                    end=desired.end.get("dateTime"),
                )
            except Exception as exc:
                failed += 1
                log.error(
                    "rubitime_slot_upsert_failed",
                    event_id=event_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

        stale_ids = existing_by_id.keys() - desired_by_id.keys()
        for event_id in sorted(stale_ids):
            try:
                calendar.delete_event(event_id)
                deleted += 1
                log.debug("rubitime_slot_deleted", event_id=event_id)
            except Exception as exc:
                failed += 1
                log.error(
                    "rubitime_slot_deletion_failed",
                    event_id=event_id,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )

        return {
            "created_events": created,
            "updated_events": updated,
            "unchanged_events": unchanged,
            "deleted_events": deleted,
            "failed_events": failed,
        }

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
        
        counters = self.reconcile_slot_events(calendar, slots)
        
        log.info(
            "rubitime_schedule_sync_finished",
            available_slots=len(slots),
            **counters,
        )
        return {
            "status": "ok",
            "available_slots": len(slots),
            **counters,
        }
