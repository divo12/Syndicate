"""Tracing lifecycle checks without running the app or exporting telemetry."""

from unittest.mock import patch

import pytest


def test_shutdown_runs_even_when_flush_raises() -> None:
    with patch("neatlogs.init"), patch("atexit.register", side_effect=lambda fn: fn):
        from syndicate.observability.tracing import shutdown_tracing

    with (
        patch("neatlogs.flush", side_effect=RuntimeError("flush failed")),
        patch("neatlogs.shutdown") as shutdown,
    ):
        with pytest.raises(RuntimeError, match="flush failed"):
            shutdown_tracing()
    shutdown.assert_called_once()


def test_shutdown_flushes_before_stopping_sdk() -> None:
    with patch("neatlogs.init"), patch("atexit.register", side_effect=lambda fn: fn):
        from syndicate.observability.tracing import shutdown_tracing

    calls: list[str] = []
    with (
        patch("neatlogs.flush", side_effect=lambda: calls.append("flush")),
        patch("neatlogs.shutdown", side_effect=lambda: calls.append("shutdown")),
    ):
        shutdown_tracing()
    assert calls == ["flush", "shutdown"]
