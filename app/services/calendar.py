"""Google Calendar API operations."""

from __future__ import annotations

import json
import re
from typing import Any
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import Settings
from app.models.google_event import GoogleEventPayload
from app.utils.google_calendar import event_response_summary, http_error_details
from app.utils.logger import compact_json, get_logger

log = get_logger(__name__)

SCOPES = ("https://www.googleapis.com/auth/calendar",)

# Deterministic event id on insert: numeric record id, optionally zero-padded.
_GOOGLE_EVENT_ID_RE = re.compile(r"^[a-v_0-9]{5,1024}$")


def validate_google_event_id(event_id: str) -> None:
    if _GOOGLE_EVENT_ID_RE.match(event_id):
        return
    raise ValueError(
        f"Некорректный Google Calendar event id: {event_id!r}. "
        "Требования API: 5–1024 символа, только lowercase a-v и цифры 0-9."
    )


class CalendarService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if not settings.google_service_account_json:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON is not set. Put the JSON key into .env "
                "or provide GOOGLE_SERVICE_ACCOUNT_JSON_FILE locally."
            )
        if not settings.google_calendar_id:
            raise ValueError("GOOGLE_CALENDAR_ID is not set")

        info = json.loads(settings.google_service_account_json)
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=SCOPES,
        )
        self._service = build(
            "calendar",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

    def _calendar_id(self) -> str:
        return self._settings.google_calendar_id

    def _log_request(self, operation: str, event_id: str, **extra: Any) -> None:
        log.info(
            "google_calendar_request",
            operation=operation,
            calendar_id=self._calendar_id(),
            google_event_id=event_id,
            **extra,
        )

    def _log_response(self, operation: str, event_id: str, result: dict[str, Any]) -> None:
        summary = event_response_summary(result)
        summary.pop("google_event_id", None)
        log.info(
            "google_calendar_response",
            operation=operation,
            google_event_id=event_id,
            **summary,
        )

    def _log_error(self, operation: str, event_id: str, exc: HttpError) -> None:
        log.error(
            "google_calendar_error",
            operation=operation,
            google_event_id=event_id,
            **http_error_details(exc),
        )

    def get_event(self, event_id: str) -> dict[str, Any]:
        """Fetch a single calendar event by id."""
        self._log_request("get", event_id)
        try:
            result = (
                self._service.events()
                .get(calendarId=self._calendar_id(), eventId=event_id)
                .execute()
            )
        except HttpError as exc:
            self._log_error("get", event_id, exc)
            raise
        self._log_response("get", event_id, result)
        return result

    def insert_event(
        self,
        event_id: str | None,
        payload: GoogleEventPayload,
    ) -> dict[str, Any]:
        """Create a calendar event, optionally with a caller-supplied id."""
        body = payload.to_calendar_body()
        effective_event_id = event_id or body.get("id") or ""
        if event_id:
            validate_google_event_id(event_id)
            body["id"] = event_id
        self._log_request(
            "insert",
            effective_event_id,
            summary=body.get("summary"),
            body=compact_json(body),
        )
        try:
            result = (
                self._service.events()
                .insert(
                    calendarId=self._calendar_id(),
                    body=body,
                )
                .execute()
            )
        except HttpError as exc:
            # Если ошибка 409 (duplicate), попытаться обновить событие
            if exc.resp.status == 409 and effective_event_id:
                log.info(
                    "google_calendar_duplicate_detected",
                    google_event_id=effective_event_id,
                    operation="updating",
                )
                try:
                    result = self.update_event(effective_event_id, payload)
                    self._log_response("update", effective_event_id, result)
                    return result
                except HttpError as update_exc:
                    self._log_error("update", effective_event_id, update_exc)
                    raise
            self._log_error("insert", effective_event_id, exc)
            raise
        self._log_response("insert", result.get("id", effective_event_id), result)
        return result

    def update_event(self, event_id: str, payload: GoogleEventPayload) -> dict[str, Any]:
        """Full replace update for existing event."""
        body = payload.to_calendar_body()
        self._log_request(
            "update",
            event_id,
            summary=body.get("summary"),
            body=compact_json(body),
        )
        try:
            result = (
                self._service.events()
                .update(
                    calendarId=self._calendar_id(),
                    eventId=event_id,
                    body=body,
                )
                .execute()
            )
        except HttpError as exc:
            self._log_error("update", event_id, exc)
            raise
        self._log_response("update", event_id, result)
        return result

    def delete_event(self, event_id: str) -> dict[str, Any]:
        """Delete calendar event; 404/410 — идемпотентный успех."""
        self._log_request("delete", event_id)
        try:
            self._service.events().delete(
                calendarId=self._calendar_id(),
                eventId=event_id,
            ).execute()
        except HttpError as exc:
            if exc.resp.status in (404, 410):
                log.info(
                    "google_calendar_delete_idempotent",
                    google_event_id=event_id,
                    http_status=exc.resp.status,
                    outcome="already_absent",
                )
                return {
                    "google_event_id": event_id,
                    "outcome": "already_absent",
                    "http_status": exc.resp.status,
                }
            self._log_error("delete", event_id, exc)
            raise
        log.info(
            "google_calendar_response",
            operation="delete",
            google_event_id=event_id,
            outcome="deleted",
        )
        return {"google_event_id": event_id, "outcome": "deleted"}

    def list_events(
        self,
        *,
        time_min: datetime | None = None,
        time_max: datetime | None = None,
        private_extended_property: str | None = None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            request_kwargs: dict[str, Any] = {
                "calendarId": self._calendar_id(),
                "singleEvents": True,
                "showDeleted": False,
                "maxResults": 2500,
            }
            if page_token:
                request_kwargs["pageToken"] = page_token
            if time_min is not None:
                request_kwargs["timeMin"] = time_min.isoformat()
            if time_max is not None:
                request_kwargs["timeMax"] = time_max.isoformat()
            if private_extended_property:
                request_kwargs["privateExtendedProperty"] = private_extended_property

            request = self._service.events().list(**request_kwargs)
            self._log_request(
                "list",
                "events",
                time_min=time_min.isoformat() if time_min else None,
                time_max=time_max.isoformat() if time_max else None,
                private_extended_property=private_extended_property,
            )
            try:
                result = request.execute()
            except HttpError as exc:
                self._log_error("list", "events", exc)
                raise
            batch = result.get("items", []) or []
            if batch:
                events.extend(batch)
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        log.info(
            "google_calendar_response",
            operation="list",
            google_event_id="events",
            result_count=len(events),
        )
        return events
