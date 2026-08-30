#!/usr/bin/env python3
"""
Интерактивный тест интеграции с Google Calendar через CalendarService.

Запуск из корня репозитория:
  python scripts/test_google_calendar.py

Переменные окружения читаются из .env (GOOGLE_CALENDAR_ID, ключ SA и т.д.).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from googleapiclient.errors import HttpError

from app.config import Settings
from app.models.google_event import GoogleEventPayload
from app.services.calendar import CalendarService


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def format_http_error(exc: HttpError) -> dict[str, Any]:
    raw = exc.content.decode("utf-8", errors="replace") if exc.content else ""
    details: dict[str, Any] = {
        "status": exc.resp.status,
        "reason": exc.reason,
        "uri": getattr(exc, "uri", None),
        "raw": raw,
    }
    if raw:
        try:
            parsed = json.loads(raw)
            details["json"] = parsed
            if isinstance(parsed, dict) and "error" in parsed:
                err = parsed["error"]
                details["message"] = err.get("message")
                details["errors"] = err.get("errors")
        except json.JSONDecodeError:
            pass
    return details


def format_exception(exc: BaseException) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc().rstrip(),
    }


def print_block(title: str, data: Any) -> None:
    print(f"\n{'=' * 60}")
    print(title)
    print("=" * 60)
    if isinstance(data, (dict, list)):
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(data)


def prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value if value else default


def build_test_payload(settings: Settings) -> GoogleEventPayload:
    """Собрать тело события из ввода в консоли."""
    tz = settings.event_timezone
    summary = prompt("summary (заголовок)", "Иван (ТЕСТ) – Не оплачено")
    description = prompt(
        "description",
        "Пациент: Иван (ТЕСТ)\nСтатус оплаты: Не оплачено",
    )
    location = prompt("location", settings.event_location or "Москва")
    color_id = prompt("colorId", settings.google_event_color_id)

    start_raw = prompt(
        "start (YYYY-MM-DD HH:MM)",
        (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:00"),
    )
    duration_min = int(prompt("длительность (минуты)", "60"))

    start_dt = datetime.strptime(start_raw, "%Y-%m-%d %H:%M")
    end_dt = start_dt + timedelta(minutes=duration_min)

    start_block = {"dateTime": start_dt.isoformat(), "timeZone": tz}
    end_block = {"dateTime": end_dt.isoformat(), "timeZone": tz}

    record_id = prompt("record_id для extendedProperties", "9999999999")

    return GoogleEventPayload(
        summary=summary,
        description=description,
        location=location,
        color_id=color_id,
        start=start_block,
        end=end_block,
        extended_properties={
            "private": {"yclients_record_id": record_id},
        },
    )


def run_operation(
    calendar: CalendarService,
    calendar_id: str,
    operation: str,
    event_id: str,
    payload: GoogleEventPayload | None,
) -> None:
    request: dict[str, Any] = {
        "operation": operation,
        "calendarId": calendar_id,
        "eventId": event_id,
    }
    if payload is not None:
        body = payload.to_calendar_body()
        if operation == "insert":
            body["id"] = event_id
        request["body"] = body

    print_block("Запрос", request)

    try:
        if operation == "insert":
            assert payload is not None
            result = calendar.insert_event(event_id, payload)
        elif operation == "update":
            assert payload is not None
            result = calendar.update_event(event_id, payload)
        elif operation == "delete":
            result = calendar.delete_event(event_id)
        elif operation == "get":
            result = calendar.get_event(event_id)
        else:
            print(f"Неизвестная операция: {operation}")
            return
    except HttpError as exc:
        print_block("Ошибка Google API", format_http_error(exc))
        return
    except ValueError as exc:
        print_block("Ошибка валидации", format_exception(exc))
        return
    except Exception as exc:
        print_block("Ошибка", format_exception(exc))
        return

    print_block("Ответ", result)
    if operation == "get" and isinstance(result, dict):
        status = result.get("status")
        if status == "cancelled":
            print(
                "\nПримечание: status=cancelled — событие удалено (мягкое удаление). "
                "GET по id всё равно возвращает «труп» записи; в UI календаря его обычно не видно. "
                "Повторный insert с тем же eventId может дать 409 — используйте новый id."
            )
        elif status == "confirmed":
            print("\nПримечание: status=confirmed — активное событие в календаре.")


def main() -> None:
    load_env_file(ROOT / ".env")
    settings = Settings.from_environ()

    print("Google Calendar — тестовая интеграция")
    print(f"  calendar_id: {settings.google_calendar_id or '(не задан)'}")
    print(f"  timezone:    {settings.event_timezone}")

    try:
        calendar = CalendarService(settings)
    except ValueError as exc:
        print(f"\nОшибка конфигурации: {exc}")
        sys.exit(1)

    default_event_id = GoogleEventPayload.google_event_id(9999999999)
    print(f"  event_id:    {default_event_id} (числовой record_id, см. Google base32hex)")

    while True:
        print("\n--- Меню ---")
        print("  1 — insert (создать)")
        print("  2 — update (обновить)")
        print("  3 — delete (удалить)")
        print("  4 — get (прочитать)")
        print("  0 — выход")
        choice = prompt("Выбор", "1")

        if choice == "0":
            break

        event_id = prompt("eventId", default_event_id)
        op_map = {"1": "insert", "2": "update", "3": "delete", "4": "get"}
        operation = op_map.get(choice)
        if not operation:
            print("Неверный пункт меню.")
            continue

        payload: GoogleEventPayload | None = None
        if operation in ("insert", "update"):
            payload = build_test_payload(settings)

        run_operation(calendar, settings.google_calendar_id, operation, event_id, payload)


if __name__ == "__main__":
    main()
