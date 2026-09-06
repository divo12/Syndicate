"""Process-scoped tracing for the controller's benchmark-task capability."""

import atexit
import inspect
import os

import neatlogs as neatlogs  # type: ignore[import-untyped]


def wrap_provider[T](client: T) -> T:
    wrapper = getattr(neatlogs, "wrap", None)
    if callable(wrapper):
        return wrapper(client)
    return client


def _init_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {"workflow_name": "solve-benchmark-task"}
    try:
        params = inspect.signature(neatlogs.init).parameters
    except (TypeError, ValueError):
        return kwargs
    if "register_shutdown_handlers" in params:
        kwargs["register_shutdown_handlers"] = False
    endpoint = os.environ.get("NEATLOGS_ENDPOINT")
    if endpoint:
        kwargs["endpoint"] = endpoint
    elif "endpoint" in params:
        default = params["endpoint"].default
        if isinstance(default, str) and "localhost" in default:
            kwargs["endpoint"] = "https://ingest.neatlogs.com"
    return kwargs


neatlogs.init(**_init_kwargs())


@atexit.register
def shutdown_tracing() -> None:
    """Flush once at process exit; server owners may call this on shutdown too."""
    try:
        neatlogs.flush()
    finally:
        neatlogs.shutdown()
