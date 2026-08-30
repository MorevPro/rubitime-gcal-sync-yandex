"""Structured logging setup."""

import json
import logging
import sys
from typing import Any

import structlog

_configured = False


def to_single_line(value: str) -> str:
    """Some container runtimes split stdout by line; keep logs single-line."""
    return value.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\r")


def compact_json(value: Any) -> str:
    """Сериализует значение в однострочный JSON."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, separators=(",", ":"))
        except json.JSONDecodeError:
            return to_single_line(text)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class _SingleLineStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = to_single_line(self.format(record))
            self.stream.write(msg + self.terminator)
            self.flush()
        except Exception:
            self.handleError(record)


def _render_single_line_json(_: Any, __: str, event_dict: dict[str, Any]) -> str:
    line = json.dumps(event_dict, ensure_ascii=False, separators=(",", ":"), default=str)
    return to_single_line(line)


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    log_level = getattr(logging, level.upper(), logging.INFO)
    handler = _SingleLineStreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logging.basicConfig(handlers=[handler], level=log_level)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _render_single_line_json,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
