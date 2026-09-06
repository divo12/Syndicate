"""Process-scoped tracing for the controller's benchmark-task capability."""

import atexit

import neatlogs as neatlogs  # type: ignore[import-untyped]

neatlogs.init(workflow_name="solve-benchmark-task", register_shutdown_handlers=False)


@atexit.register
def shutdown_tracing() -> None:
    """Flush once at process exit; server owners may call this on shutdown too."""
    try:
        neatlogs.flush()
    finally:
        neatlogs.shutdown()
