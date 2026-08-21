"""Explicit, redacted logging configuration for the application."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class SensitiveDataFilter(logging.Filter):
    """Redact common credentials before a record reaches any handler."""

    _assignment_pattern = re.compile(
        r"(?i)\b(password|passwd|token|api[_-]?key|secret)\s*([:=])\s*([^\s,;]+)"
    )
    _url_pattern = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@", re.I)
    _windows_home_pattern = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\/\s]+")
    _posix_home_pattern = re.compile(r"(?<!\w)/home/[^/\s]+")

    @classmethod
    def redact(cls, value: str) -> str:
        value = cls._assignment_pattern.sub(r"\1\2<redacted>", value)
        value = cls._url_pattern.sub(r"\g<scheme><redacted>@", value)
        value = cls._windows_home_pattern.sub("%USERPROFILE%", value)
        return cls._posix_home_pattern.sub("$HOME", value)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


class StructuredJsonFormatter(logging.Formatter):
    """Render one redacted, machine-readable event per line."""

    _standard_attributes = frozenset(logging.makeLogRecord({}).__dict__)

    def __init__(self, session_id: str) -> None:
        super().__init__()
        self._session_id = session_id

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": SensitiveDataFilter.redact(record.getMessage()),
            "session_id": self._session_id,
        }
        for key, value in record.__dict__.items():
            if key in self._standard_attributes or key in {"message", "asctime"}:
                continue
            payload[key] = self._redact_value(value)
        if record.exc_info:
            payload["exception"] = SensitiveDataFilter.redact(
                self.formatException(record.exc_info)
            )
        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))

    @classmethod
    def _redact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return SensitiveDataFilter.redact(value)
        if isinstance(value, dict):
            return {str(key): cls._redact_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [cls._redact_value(item) for item in value]
        return value


class LoggingConfigurator:
    """Configure only the AIOpenStudio logger and leave libraries untouched."""

    def __init__(self, logger_name: str = "aiopenstudio") -> None:
        self.logger_name = logger_name

    def configure(
        self,
        *,
        level: str,
        log_dir: Path,
        session_id: str,
        console: bool = True,
    ) -> logging.Logger:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(self.logger_name)
        logger.setLevel(level)
        logger.propagate = False

        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

        console_formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        json_formatter = StructuredJsonFormatter(session_id)
        redactor = SensitiveDataFilter()

        file_handler = RotatingFileHandler(
            log_dir / "aiopenstudio.jsonl",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(json_formatter)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)

        if console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(console_formatter)
            console_handler.addFilter(redactor)
            logger.addHandler(console_handler)

        return logger
