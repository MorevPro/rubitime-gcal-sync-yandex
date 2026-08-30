"""Webhook processing logic."""

from typing import Any

from googleapiclient.errors import HttpError

from app.config import get_settings
from app.models.google_event import GoogleEventPayload
from app.services.calendar import CalendarService
from app.services.formatter import EventFormatter
from app.utils.google_calendar import event_response_summary, http_error_details
from app.utils.logger import get_logger

log = get_logger(__name__)


def google_event_id(record_id: int, event: str | None = None) -> str:
    return GoogleEventPayload.google_event_id(record_id, event)


def _upsert_event(
    calendar: CalendarService,
    event_id: str,
    payload: GoogleEventPayload,
    *,
    prefer: str,
) -> tuple[dict[str, Any], str]:
    """CREATE/UPDATE: insert или update с идемпотентными fallback (TASK §30)."""
    if prefer == "insert":
        try:
            result = calendar.insert_event(event_id, payload)
            return result, "insert"
        except HttpError as exc:
            if exc.resp.status == 409:
                log.info(
                    "google_upsert_fallback",
                    google_event_id=event_id,
                    from_action="insert",
                    to_action="update",
                    reason="duplicate",
                    **http_error_details(exc),
                )
                result = calendar.update_event(event_id, payload)
                return result, "update_after_409"
            raise

    try:
        result = calendar.update_event(event_id, payload)
        return result, "update"
    except HttpError as exc:
        if exc.resp.status == 404:
            log.info(
                "google_upsert_fallback",
                google_event_id=event_id,
                from_action="update",
                to_action="insert",
                reason="not_found",
                **http_error_details(exc),
            )
            result = calendar.insert_event(event_id, payload)
            return result, "insert_after_404"
        raise


def _log_finished(
    *,
    operation: str,
    record_id: int,
    event_id: str,
    outcome: str,
    google_action: str,
    result: dict[str, Any] | None = None,
) -> None:
    fields: dict[str, Any] = {
        "operation": operation,
        "record_id": record_id,
        "google_event_id": event_id,
        "status": "success",
        "google_action": google_action,
        "outcome": outcome,
    }
    if result is not None:
        fields.update(event_response_summary(result))
    log.info("webhook_processing_finished", **fields)


def process_webhook(event: str, record_id: int, data: dict[str, Any]) -> None:
    """Background worker: sync record to Google Calendar."""
    settings = get_settings()
    calendar = CalendarService(settings)
    formatter = EventFormatter(settings)
    event_id = google_event_id(record_id, event)

    log.info(
        "webhook_processing_started",
        operation=event,
        record_id=record_id,
        google_event_id=event_id,
    )

    try:
        if event == "event-create-record":
            payload = formatter.build(record_id, data, event)
            result, google_action = _upsert_event(
                calendar, event_id, payload, prefer="insert"
            )
            _log_finished(
                operation=event,
                record_id=record_id,
                event_id=event_id,
                outcome="synced",
                google_action=google_action,
                result=result,
            )
        elif event == "event-update-record":
            payload = formatter.build(record_id, data, event)
            result, google_action = _upsert_event(
                calendar, event_id, payload, prefer="update"
            )
            _log_finished(
                operation=event,
                record_id=record_id,
                event_id=event_id,
                outcome="synced",
                google_action=google_action,
                result=result,
            )
        elif event == "event-remove-record":
            delete_result = calendar.delete_event(event_id)
            _log_finished(
                operation=event,
                record_id=record_id,
                event_id=event_id,
                outcome=delete_result.get("outcome", "deleted"),
                google_action="delete",
                result=delete_result,
            )
        else:
            log.warning(
                "webhook_unknown_event",
                operation=event,
                record_id=record_id,
                google_event_id=event_id,
            )
    except HttpError as exc:
        log.error(
            "webhook_processing_failed",
            operation=event,
            record_id=record_id,
            google_event_id=event_id,
            error_type="HttpError",
            **http_error_details(exc),
        )
    except Exception as exc:
        log.exception(
            "webhook_processing_failed",
            operation=event,
            record_id=record_id,
            google_event_id=event_id,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
