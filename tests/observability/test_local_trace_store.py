from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from syndicate.budget_policy import ProductRole
from syndicate.observability.models import (
    CaptureText,
    SpanKind,
    SpanStatus,
    TraceLink,
    TraceManifest,
    TraceSpan,
)
from syndicate.observability.store import LocalTraceStore


def span() -> TraceSpan:
    return TraceSpan(
        trace_id=UUID("11111111-1111-1111-1111-111111111111"),
        span_id=UUID("22222222-2222-2222-2222-222222222222"),
        parent_span_id=None,
        role=ProductRole.EXECUTOR,
        kind=SpanKind.TOOL,
        status=SpanStatus.OK,
        tool_name="shell",
        entity_id="ticket:1",
        started_at=datetime(2026, 9, 6, tzinfo=UTC),
        ended_at=datetime(2026, 9, 6, tzinfo=UTC),
        request=CaptureText(raw="raw command", model_visible="command", complete=True),
        response=CaptureText(
            raw="full output", model_visible="[clipped]", complete=True
        ),
    )


def link() -> TraceLink:
    return TraceLink(
        operation_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        attempt_id=UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"),
        run_id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
    )


def test_store_preserves_raw_and_exact_model_visible_capture(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    original = span()

    store.record(original)

    assert store.read(original.trace_id, original.span_id) == original


def test_seal_is_immutable_and_reports_missing_spans(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    original = span()
    store.record(original)
    missing = UUID("33333333-3333-3333-3333-333333333333")

    manifest = store.seal(original.trace_id, (original.span_id, missing), link())

    assert not manifest.complete
    assert manifest.missing_span_ids == (missing,)
    assert manifest.missing_reasons == (f"missing-span:{missing}",)
    assert store.read_manifest(original.trace_id) == manifest
    span_path = tmp_path / str(original.trace_id) / f"{original.span_id}.json"
    span_path.write_text(
        original.model_copy(update={"span_id": missing}).model_dump_json()
    )
    with pytest.raises(ValueError, match="requested IDs"):
        store.read_manifest(original.trace_id)
    with pytest.raises(ValueError, match="sealed"):
        store.record(original.model_copy(update={"span_id": missing}))


def test_empty_trace_seals_an_explicit_all_missing_manifest(tmp_path: Path) -> None:
    store = LocalTraceStore(tmp_path)
    trace_id = UUID("44444444-4444-4444-4444-444444444444")
    missing = UUID("55555555-5555-5555-5555-555555555555")

    manifest = store.seal(trace_id, (missing,), link())

    assert not manifest.complete
    assert manifest.missing_span_ids == (missing,)
    assert store.read_manifest(trace_id) == manifest


def test_manifest_rejects_a_naive_sealed_timestamp(tmp_path: Path) -> None:
    manifest = LocalTraceStore(tmp_path).seal(
        UUID("66666666-6666-6666-6666-666666666666"), (), link()
    )

    with pytest.raises(ValidationError):
        TraceManifest.model_validate(
            manifest.model_dump() | {"sealed_at": datetime(2026, 9, 6)}
        )
