"""Explicit, redacted logging configuration for the application."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path


class SensitiveDataFilter(logging.Filter):
    """Redact common credentials before a record reaches any handler."""

    _assignment_pattern = re.compile(
        r"(?i)\b(password|passwd|token|api[_-]?key|secret)\s*([:=])\s*([^\s,;]+)"
    )
    _url_pattern = re.compile(r"(?P<scheme>[a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@", re.I)

    @classmethod
    def redact(cls, value: str) -> str:
        value = cls._assignment_pattern.sub(r"\1\2<redacted>", value)
        return cls._url_pattern.sub(r"\g<scheme><redacted>@", value)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(record.getMessage())
        record.args = ()
        return True


class LoggingConfigurator:
    """Configure only the AIOpenStudio logger and leave libraries untouched."""

    def __init__(self, logger_name: str = "aiopenstudio") -> None:
        self.logger_name = logger_name

    def configure(
        self,
        *,
        level: str,
        log_dir: Path,
        console: bool = True,
    ) -> logging.Logger:
        log_dir.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(self.logger_name)
        logger.setLevel(level)
        logger.propagate = False

        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)

        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
        redactor = SensitiveDataFilter()

        file_handler = RotatingFileHandler(
            log_dir / "aiopenstudio.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        logger.addHandler(file_handler)

        if console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            console_handler.addFilter(redactor)
            logger.addHandler(console_handler)

        return logger
