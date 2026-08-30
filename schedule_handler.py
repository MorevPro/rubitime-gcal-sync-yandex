"""Yandex Cloud Function entrypoint: periodic Rubitime free-slots sync.

Разверните как отдельную функцию с entrypoint `schedule_handler.handler` и
привяжите к ней Yandex Cloud Trigger типа Timer (cron), например
`*/5 * * * ? *` — это заменяет фоновый asyncio-цикл из исходного сервиса
(`RUBITIME_SYNC_INTERVAL_SECONDS` там больше не используется: интервал
задаётся расписанием триггера).
"""

from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.services.rubitime_schedule import RubitimeScheduleSyncService
from app.utils.logger import configure_logging, get_logger

settings = get_settings()
configure_logging(settings.log_level)
log = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    sync_service = RubitimeScheduleSyncService(settings)

    if not sync_service.is_configured():
        log.info("rubitime_scheduler_disabled", reason="missing_configuration")
        return {"statusCode": 200, "body": "skipped: missing_configuration"}

    result = sync_service.sync_once()
    return {"statusCode": 200, "body": str(result)}
