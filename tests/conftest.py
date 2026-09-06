"""Finish telemetry while pytest's captured logging streams are still available."""

import atexit
import sys


def pytest_sessionfinish() -> None:
    if "syndicate.observability.tracing" in sys.modules:
        from syndicate.observability.tracing import shutdown_tracing

        atexit.unregister(shutdown_tracing)
        shutdown_tracing()
