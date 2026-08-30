"""Yandex Cloud Function entrypoint: Rubitime webhook -> Google Calendar.

Разверните как отдельную функцию с entrypoint `webhook_handler.handler`.
В отличие от исходного FastAPI-сервиса, где синхронизация с Google Calendar
выполнялась в фоновой asyncio-задаче после мгновенного ответа, здесь она
выполняется синхронно в теле того же вызова: serverless-функция не может
продолжать работу после возврата ответа. `process_webhook` сам перехватывает
и логирует все исключения, поэтому ответ `ok: true` возвращается независимо
от результата синхронизации — как и в оригинале.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from pydantic import ValidationError

from app.config import get_settings
from app.models.google_event import GoogleEventPayload
from app.models.webhook import WebhookPayload
from app.routes.webhook import process_webhook
from app.utils.logger import compact_json, configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger(__name__)


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _raw_body(event: dict[str, Any]) -> str:
    body = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            return base64.b64decode(body).decode("utf-8", errors="replace")
        except Exception as exc:
            return f"<base64 decode error: {exc}>"
    return body


def _load_json(text: str) -> Any | None:
    candidate = text.strip().lstrip("﻿")
    if not candidate:
        return None
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _parse_body(raw_body: str) -> dict[str, Any] | None:
    parsed = _load_json(raw_body)
    if isinstance(parsed, dict):
        nested_body = parsed.get("body")
        if isinstance(nested_body, str):
            nested = _load_json(nested_body)
            if isinstance(nested, dict):
                return nested
        return parsed

    # Некоторые релеи webhook'ов оборачивают реальный payload как JSON-строку
    # в поле верхнего уровня `body`. Поддерживаем это как fallback.
    if isinstance(parsed, str):
        nested = _load_json(parsed)
        if isinstance(nested, dict):
            return nested

    return None


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    method = event.get("httpMethod", "POST")
    path = event.get("url") or event.get("path") or "/webhook"
    headers = event.get("headers") or {}
    content_type = headers.get("Content-Type") or headers.get("content-type") or ""

    if method == "GET":
        return _response(200, {"ok": True, "service": "rubitime-gcal-sync-yandex"})

    raw_body = _raw_body(event)

    log.info(
        "webhook_received",
        method=method,
        path=path,
        content_type=content_type,
        body_size=len(raw_body),
        body=compact_json(raw_body),
    )

    payload_dict = _parse_body(raw_body)
    if not payload_dict:
        log.warning(
            "webhook_empty_body",
            content_type=content_type,
            body_size=len(raw_body),
        )
        return _response(400, {"ok": False, "error": "empty_or_invalid_body"})

    try:
        payload = WebhookPayload.model_validate(payload_dict)
    except ValidationError as exc:
        log.warning("webhook_validation_failed", errors=exc.errors())
        return _response(400, {"ok": False, "error": "validation_failed"})

    event_name = payload.event
    record_id = payload.record_id
    data = payload.data.model_dump(mode="python", exclude_none=False)

    log.info(
        "webhook_accepted",
        operation=event_name,
        record_id=record_id,
        google_event_id=GoogleEventPayload.google_event_id(record_id, event_name),
    )

    process_webhook(event_name, record_id, data)

    return _response(
        200,
        {
            "ok": True,
            "accepted": True,
            "record_id": record_id,
            "event": event_name,
        },
    )
