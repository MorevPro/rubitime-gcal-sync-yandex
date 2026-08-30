"""Application settings from environment variables and local .env."""

import base64
import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field


_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=False, encoding="utf-8-sig")


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def _load_google_service_account_json() -> str:
    """JSON key: env string, base64, or file path (GOOGLE_SERVICE_ACCOUNT_JSON_FILE)."""
    raw = _env("GOOGLE_SERVICE_ACCOUNT_JSON").strip()
    if raw:
        if raw.startswith("{"):
            return raw
        try:
            return base64.b64decode(raw).decode("utf-8")
        except Exception:
            return raw

    file_path = _env("GOOGLE_SERVICE_ACCOUNT_JSON_FILE").strip()
    if file_path:
        # Используется только для локального запуска/тестов. В Yandex Cloud
        # передавайте ключ через GOOGLE_SERVICE_ACCOUNT_JSON (env/Lockbox-секрет).
        path = Path(file_path)
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / file_path
        if path.is_file():
            return path.read_text(encoding="utf-8")

    return ""


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Settings(BaseModel):
    google_calendar_id: str = Field(default="")
    google_service_account_json: str = Field(default="")
    google_event_color_id: str = Field(default="5")
    google_long_event_color_id: str = Field(default="9")
    rubitime_api_key: str = Field(default="")
    rubitime_branch_id: int = Field(default=0)
    rubitime_cooperator_id: int = Field(default=0)
    rubitime_service_id: int = Field(default=0)
    rubitime_only_available: bool = Field(default=True)
    event_timezone: str = Field(default="Europe/Moscow")
    event_location: str = Field(default="Москва")
    calendar_summary_template: str = Field(default="{payment_prefix}{name} | {price}")
    log_level: str = Field(default="INFO")

    @classmethod
    def from_environ(cls) -> "Settings":
        return cls(
            google_calendar_id=_env("GOOGLE_CALENDAR_ID"),
            google_service_account_json=_load_google_service_account_json(),
            google_event_color_id=_env("GOOGLE_EVENT_COLOR_ID", "5"),
            google_long_event_color_id=_env("GOOGLE_LONG_EVENT_COLOR_ID", "9"),
            rubitime_api_key=_env("RUBITIME_API_KEY"),
            rubitime_branch_id=_env_int("RUBITIME_BRANCH_ID", 0),
            rubitime_cooperator_id=_env_int("RUBITIME_COOPERATOR_ID", 0),
            rubitime_service_id=_env_int("RUBITIME_SERVICE_ID", 0),
            rubitime_only_available=_env_bool("RUBITIME_ONLY_AVAILABLE", True),
            event_timezone=_env("EVENT_TIMEZONE", "Europe/Moscow"),
            event_location=_env(
                "EVENT_LOCATION",
                "Москва",
            ),
            calendar_summary_template=_env(
                "CALENDAR_SUMMARY_TEMPLATE",
                "{payment_prefix}{name} | {price}",
            ),
            log_level=_env("LOG_LEVEL", "INFO"),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environ()
