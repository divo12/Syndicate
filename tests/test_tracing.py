"""Tracing lifecycle checks without running the app or exporting telemetry."""

from unittest.mock import patch
from uuid import UUID

import pytest

from syndicate.observability.neatlogs_capture import (
    CaptureState,
    NeatlogsCapture,
    RunLink,
)


@pytest.mark.parametrize(
    ("flushed", "state"),
    [(True, CaptureState.FLUSHED_UNVERIFIED), (False, CaptureState.BLOCKED)],
)
def test_flush_receipt_reflects_sdk_result(flushed: bool, state: CaptureState) -> None:
    link = RunLink(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        run_id=UUID(int=3),
        task_id="task-a-1",
    )
    with patch("neatlogs.flush", return_value=flushed):
        assert NeatlogsCapture("solve-benchmark-task").flush(link).state == state


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
