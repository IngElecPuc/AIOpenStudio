import logging

from aiopenstudio.services.logging import SensitiveDataFilter


def test_sensitive_values_are_redacted() -> None:
    record = logging.LogRecord(
        name="aiopenstudio.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="token=abc password:secret postgresql://user:pass@localhost/db",
        args=(),
        exc_info=None,
    )

    assert SensitiveDataFilter().filter(record)
    rendered = record.getMessage()

    assert "abc" not in rendered
    assert "secret" not in rendered
    assert "user:pass" not in rendered
    assert rendered.count("<redacted>") == 3
