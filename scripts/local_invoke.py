#!/usr/bin/env python3
"""Ручной вызов обработчиков функций локально, без деплоя в Yandex Cloud.

Запуск из корня репозитория:
    python scripts/local_invoke.py webhook '{"event":"event-update-record","data":{"id":123,"parent_record":null,"name":"Test","record":"2026-08-20 15:00:00"}}'
    python scripts/local_invoke.py schedule
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in {"webhook", "schedule"}:
        print(__doc__)
        sys.exit(1)

    target = sys.argv[1]

    if target == "webhook":
        import webhook_handler

        body = sys.argv[2] if len(sys.argv) > 2 else "{}"
        event = {
            "httpMethod": "POST",
            "headers": {"Content-Type": "application/json"},
            "body": body,
            "isBase64Encoded": False,
        }
        result = webhook_handler.handler(event, None)
    else:
        import schedule_handler

        result = schedule_handler.handler({}, None)

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
