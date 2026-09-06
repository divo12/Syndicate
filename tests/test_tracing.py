"""Tracing lifecycle checks without running the app or exporting telemetry."""

from unittest.mock import patch

import pytest

from syndicate.observability.tracing import _init_kwargs


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


def test_wrap_provider_skips_missing_sdk_wrap() -> None:
    from syndicate.observability.tracing import wrap_provider

    client = object()
    with patch("syndicate.observability.tracing.neatlogs") as sdk:
        del sdk.wrap
        assert wrap_provider(client) is client


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


def test_hosted_endpoint_replaces_localhost_batch_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEATLOGS_ENDPOINT", raising=False)

    def init(endpoint: str = "http://localhost:3000/api/data/v4/batch") -> None:
        del endpoint

    with patch("syndicate.observability.tracing.neatlogs") as sdk:
        sdk.init = init
        kwargs = _init_kwargs()
    assert kwargs["endpoint"] == "https://ingest.neatlogs.com"
    assert "api/data/v4/batch" not in str(kwargs["endpoint"])


def test_explicit_endpoint_env_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEATLOGS_ENDPOINT", "https://ingest.example/v1/traces")
    assert _init_kwargs()["endpoint"] == "https://ingest.example/v1/traces"
