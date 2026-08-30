"""Build Google Calendar event body from rubitime record data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.config import Settings
from app.models.google_event import GoogleEventPayload
from app.utils.datetime import end_from_seance_length, parse_start_datetime


def _is_present(value: Any) -> bool:
    return value not in (None, "", [], {}, ())


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_present(*values: Any) -> Any:
    for value in values:
        if _is_present(value):
            return value
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _format_money(value: Any) -> str:
    amount = _coerce_int(value)
    if amount is None:
        amount = 0
    return f"{amount:,}".replace(",", " ") + " ₽"


def _is_paid(status: Any) -> bool:
    return _coerce_int(status) == 0


def _payment_prefix(status: Any) -> str:
    return f"{_payment_icon(status)} "


def _payment_icon(status: Any) -> str:
    return "✅" if _is_paid(status) else "❌"


def _payment_status(status: Any) -> str:
    return "Да" if _is_paid(status) else "Нет"


def _parse_datetime_text(value: Any, timezone: str) -> datetime | None:
    raw = _as_str(value)
    if not raw:
        return None
    try:
        return parse_start_datetime(raw, timezone)
    except Exception:
        return None


class EventFormatter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def build(
        self,
        record_id: int,
        data: dict[str, Any],
        event: str | None = None,
    ) -> GoogleEventPayload:
        """Map webhook data to GoogleEventPayload."""
        tz = self._settings.event_timezone

        name = _as_str(data.get("name")) or "Без имени"
        price = _coerce_int(data.get("price"))
        duration_minutes = _coerce_int(data.get("duration")) or 0
        status = data.get("status")
        payment_prefix = _payment_prefix(status)
        payment_text = _payment_status(status)
        payment_icon = _payment_icon(status)

        start_raw = _first_present(data.get("record"), data.get("date"))
        start_dt = _parse_datetime_text(start_raw, tz)
        
        # Если не удалось распарсить дату записи, пробуем создать событие с текущим временем
        if start_dt is None:
            start_dt = datetime.now(timezone(timedelta(hours=3)))
        
        end_dt = (
            end_from_seance_length(start_dt, duration_minutes)
            if duration_minutes > 0
            else None
        )
        if end_dt is None:
            end_dt = end_from_seance_length(start_dt, 60)

        start_block: dict[str, Any] = {}
        end_block: dict[str, Any] = {}
        if start_dt is not None:
            start_block = {"dateTime": start_dt.isoformat(), "timeZone": tz}
        if end_dt is not None:
            end_block = {"dateTime": end_dt.isoformat(), "timeZone": tz}

        service_title = _as_str(data.get("service_title"))
        if not service_title:
            service_title = "Услуга"

        lines: list[str] = [
            f"Оплачено: {payment_text}",
            f"Заказана услуга: {service_title} по цене {_format_money(price)}",
            f"Имя клиента: {_as_str(data.get('name'))}",
            f"Телефон клиента: {_as_str(data.get('phone'))}",
            f"Email клиента: {_as_str(data.get('email'))}",
            f"Дата рождения: {_as_str(data.get('custom_field1'))}",
        ]

        # Дополнительные поля: дата рождения и имя ребенка (отображаются только если не пустые)
        custom_field3 = _as_str(data.get('custom_field3'))
        if custom_field3:
            lines.append(f"Дата рождения ребенка: {custom_field3}")

        custom_field4 = _as_str(data.get('custom_field4'))
        if custom_field4:
            lines.append(f"Имя ребенка: {custom_field4}")

        lines.extend([
            f"Пол: {_as_str(data.get('custom_field2'))}",
            f"Комментарий клиента: {_as_str(data.get('comment'))}",
            f"Комментарий из карточки клиента: {_as_str(data.get('cardcomment') or data.get('card_comment') or data.get('client_comment'))}",
            "",
            f"Открыть запись: https://rubitime.ru/profile/analytic/history/{record_id}",
        ])

        description = "\n".join(lines).rstrip()

        template = self._settings.calendar_summary_template
        summary = template.format(
            name=name,
            payment_prefix=payment_prefix,
            payment_icon=payment_icon,
            price=_format_money(price),
        )

        source_url = _as_str(data.get("url"))
        admin_url = f"https://rubitime.ru/profile/analytic/history/{record_id}"
        # Используем ссылку на админку как source для быстрого перехода из календаря
        source: dict[str, str] = {
            "title": "Открыть запись",
            "url": admin_url,
        }

        extended_private: dict[str, str] = {
            "rubitime_record_id": str(record_id),
        }
        event_value = _as_str(event)
        if event_value:
            extended_private["rubitime_event"] = event_value
        if source_url:
            extended_private["rubitime_source_url"] = source_url

        attendees: list[dict[str, str]] = []

        return GoogleEventPayload(
            summary=summary,
            description=description,
            location=_as_str(data.get("branch_title")) or self._settings.event_location,
            color_id=(
                self._settings.google_long_event_color_id
                if duration_minutes > 60
                else self._settings.google_event_color_id
            ),
            start=start_block,
            end=end_block,
            attendees=attendees,
            source=source,
            extended_properties={"private": extended_private},
        )
