from aiopenstudio.ui.help import HELP_TOPICS


def test_built_in_help_covers_daily_operation_and_recovery() -> None:
    by_key = {topic.key: topic.body for topic in HELP_TOPICS}

    assert {"getting-started", "data", "persistence", "troubleshooting", "support"} <= set(
        by_key
    )
    assert "SQLite" in by_key["persistence"]
    assert "PostgreSQL" in by_key["persistence"]
    assert "OOM" in by_key["troubleshooting"]
    assert "prompts" in by_key["support"]
