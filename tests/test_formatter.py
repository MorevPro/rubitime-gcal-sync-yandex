from __future__ import annotations

from copy import deepcopy

from app.config import Settings
from app.models.webhook import WebhookPayload
from app.services.formatter import EventFormatter


def _settings(**overrides: object) -> Settings:
    base = dict(
        google_event_color_id="5",
        google_long_event_color_id="9",
        event_timezone="Europe/Moscow",
        event_location="Москва, улица Мещерякова, 8, офис 5",
        calendar_summary_template="{payment_icon} {name}",
        log_level="INFO",
        app_host="0.0.0.0",
        app_port=8000,
    )
    base.update(overrides)
    return Settings(**base)


CREATE_PAYLOAD = {
    "from": "user",
    "event": "event-create-record",
    "data": {
        "id": "8903326",
        "parent_record": None,
        "whom": 0,
        "created_at": "2026-08-16 13:53:49",
        "updated_at": None,
        "record": "2026-08-22 16:00:00",
        "name": "ИВАН ИВАНОВ ИВАНОВИЧ",
        "price": "10000",
        "phone": "+79151807013",
        "email": "757988@mail.ru",
        "comment": "TEST TEST",
        "status": "3",
        "status_title": "Ожидание предоплаты",
        "cooperator_id": 39824,
        "cooperator_title": "Задорина-Негода Галина Николаевна",
        "branch_id": 19697,
        "branch_title": "г. Москва, М. Тушинкая ул. Мещерякова, 8, каб. 5",
        "service_id": 71518,
        "service_title": "Дети от 1 до 18 лет (персональная тренировка)",
        "duration": "50",
        "prepayment": "10000",
        "custom_field1": "26.10.1985",
        "custom_field2": "М",
        "custom_field3": "01.01.1988",
        "custom_field4": "Имя ребенка ",
        "url": "https://zngn.rubitime.ru/widget/card/130ef1efae6f719b97b0d5573f20d31349dbf7dc7faefda6be9e533d46f14f59",
    },
}


def test_webhook_model_normalizes_string_fields() -> None:
    payload = WebhookPayload.model_validate(deepcopy(CREATE_PAYLOAD))

    assert payload.record_id == 8903326
    assert payload.data.price == 10000
    assert payload.data.status == 3
    assert payload.data.duration == 50
    assert payload.data.custom_field3 == "01.01.1988"
    assert payload.data.custom_field4 == "Имя ребенка "


def _build(data_overrides: dict[str, object] | None = None):
    formatter = EventFormatter(_settings())
    data = deepcopy(CREATE_PAYLOAD["data"])
    if data_overrides:
        data.update(data_overrides)
    return formatter.build(payload_record_id(data), data, CREATE_PAYLOAD["event"])


def payload_record_id(data: dict[str, object]) -> int:
    return int(data["id"])


def test_builds_google_event_from_webhook_payload() -> None:
    payload = _build()

    assert payload.summary == "❌ ИВАН ИВАНОВ ИВАНОВИЧ"
    assert payload.location == "г. Москва, М. Тушинкая ул. Мещерякова, 8, каб. 5"
    assert payload.color_id == "5"
    assert payload.attendees == []
    assert payload.extended_properties["private"]["rubitime_record_id"] == "8903326"
    assert payload.extended_properties["private"]["rubitime_event"] == "event-create-record"
    assert payload.extended_properties["private"]["rubitime_source_url"] == (
        "https://zngn.rubitime.ru/widget/card/130ef1efae6f719b97b0d5573f20d31349dbf7dc7faefda6be9e533d46f14f59"
    )
    assert payload.start["dateTime"] == "2026-08-22T16:00:00+03:00"
    assert payload.end["dateTime"] == "2026-08-22T16:50:00+03:00"
    assert payload.source == {
        "title": "Открыть запись",
        "url": "https://rubitime.ru/profile/analytic/history/8903326",
    }


def test_description_includes_child_fields_when_present() -> None:
    payload = _build()

    assert "Дата рождения ребенка: 01.01.1988" in payload.description
    assert "Имя ребенка: Имя ребенка" in payload.description


def test_description_omits_child_fields_when_empty() -> None:
    payload = _build({"custom_field3": None, "custom_field4": ""})

    assert "Дата рождения ребенка" not in payload.description
    assert "Имя ребенка" not in payload.description


def test_child_fields_appear_right_after_birth_date_and_before_gender() -> None:
    payload = _build()
    lines = payload.description.splitlines()

    birth_date_idx = lines.index("Дата рождения: 26.10.1985")
    child_birth_idx = lines.index("Дата рождения ребенка: 01.01.1988")
    child_name_idx = lines.index("Имя ребенка: Имя ребенка")
    gender_idx = lines.index("Пол: М")

    assert birth_date_idx < child_birth_idx < child_name_idx < gender_idx


def test_google_calendar_body_keeps_admin_link_as_source() -> None:
    payload = _build()
    body = payload.to_calendar_body()

    assert body["source"] == {
        "title": "Открыть запись",
        "url": "https://rubitime.ru/profile/analytic/history/8903326",
    }


def test_uses_false_icon_for_non_full_prepayment() -> None:
    payload = _build({"status": "3"})

    assert payload.summary == "❌ ИВАН ИВАНОВ ИВАНОВИЧ"
    assert "Оплачено: Нет" in payload.description


def test_marks_paid_records_with_check_icon() -> None:
    payload = _build({"status": "0"})

    assert payload.summary == "✅ ИВАН ИВАНОВ ИВАНОВИЧ"
    assert "Оплачено: Да" in payload.description


def test_supports_summary_template_override() -> None:
    formatter = EventFormatter(_settings(calendar_summary_template="{name} {payment_icon}"))
    data = deepcopy(CREATE_PAYLOAD["data"])
    data["status"] = "0"

    payload = formatter.build(payload_record_id(data), data, CREATE_PAYLOAD["event"])

    assert payload.summary == "ИВАН ИВАНОВ ИВАНОВИЧ ✅"


def test_falls_back_to_one_hour_when_duration_is_missing() -> None:
    data = deepcopy(CREATE_PAYLOAD["data"])
    data.pop("duration")

    payload = _build_with_data(data)

    assert payload.end["dateTime"] == "2026-08-22T17:00:00+03:00"


def test_uses_blue_color_for_records_longer_than_one_hour() -> None:
    payload = _build({"duration": "61"})

    assert payload.color_id == "9"


def test_keeps_regular_color_for_records_up_to_one_hour() -> None:
    payload = _build({"duration": "60"})

    assert payload.color_id == "5"


def _build_with_data(data: dict[str, object]):
    formatter = EventFormatter(_settings())
    return formatter.build(payload_record_id(data), data, CREATE_PAYLOAD["event"])


def test_missing_start_datetime_does_not_crash() -> None:
    payload = _build({"record": "not-a-date"})

    assert payload.start.get("dateTime")
    assert payload.end.get("dateTime")
