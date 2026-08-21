import json
import logging

from aiopenstudio.services.logging import SensitiveDataFilter, StructuredJsonFormatter


def test_sensitive_values_are_redacted() -> None:
    record = logging.LogRecord(
        name="aiopenstudio.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=(
            "token=abc password:secret postgresql://user:pass@localhost/db "
            r"C:\Users\private-name\Documents"
        ),
        args=(),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record)
    rendered = record.getMessage()

    assert "abc" not in rendered
    assert "secret" not in rendered
    assert "user:pass" not in rendered
    assert "private-name" not in rendered
    assert rendered.count("<redacted>") == 3


def test_structured_formatter_redacts_nested_metadata() -> None:
    record = logging.LogRecord(
        name="aiopenstudio.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="operation.completed",
        args=(),
        exc_info=None,
    )
    record.operation_id = "operation-1"
    record.context = {"token": "token=private", "path": r"C:\Users\person\data"}

    payload = json.loads(StructuredJsonFormatter("session-1").format(record))

    assert payload["event"] == "operation.completed"
    assert payload["session_id"] == "session-1"
    assert payload["operation_id"] == "operation-1"
    assert "private" not in json.dumps(payload)
    assert "person" not in json.dumps(payload)
