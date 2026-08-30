"""Разбор ответов и ошибок Google Calendar API для логов."""

from __future__ import annotations

import json
from typing import Any

from googleapiclient.errors import HttpError


def http_error_details(exc: HttpError) -> dict[str, Any]:
    """Поля для structlog при HttpError."""
    details: dict[str, Any] = {
        "http_status": exc.resp.status,
        "http_reason": exc.reason,
        "uri": getattr(exc, "uri", None),
    }
    raw = exc.content.decode("utf-8", errors="replace") if exc.content else ""
    if not raw:
        return details
    details["raw"] = raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return details
    err = parsed.get("error") if isinstance(parsed, dict) else None
    if isinstance(err, dict):
        details["google_message"] = err.get("message")
        details["google_errors"] = err.get("errors")
        details["google_code"] = err.get("code")
    return details


def event_response_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Краткое представление events.insert/update/get для логов."""
    return {
        "google_event_id": result.get("id"),
        "google_status": result.get("status"),
        "summary": result.get("summary"),
        "html_link": result.get("htmlLink"),
        "etag": result.get("etag"),
        "updated": result.get("updated"),
    }
