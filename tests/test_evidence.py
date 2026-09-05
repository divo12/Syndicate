"""Controller-scoped evidence queries over the real local capture store."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from syndicate.budget_policy import ProductRole
from syndicate.evidence import EvidenceReader
from syndicate.evidence_contracts import TraceGrant, TraceQuery
from syndicate.observability.models import (
    CaptureText,
    SpanKind,
    SpanStatus,
    TraceLink,
    TraceManifest,
    TraceSpan,
)
from syndicate.observability.store import LocalTraceStore


def sealed(
    root: Path, texts: tuple[str | None, ...], missing: bool = False
) -> tuple[LocalTraceStore, TraceManifest]:
    store = LocalTraceStore(root)
    trace_id = UUID(int=1)
    ids = tuple(UUID(int=index + 10) for index in range(len(texts)))
    for index, span_id in enumerate(ids):
        text = texts[index]
        store.record(
            TraceSpan(
                trace_id=trace_id,
                span_id=span_id,
                parent_span_id=None,
                role=ProductRole.EXECUTOR,
                kind=SpanKind.TOOL,
                status=SpanStatus.OK,
                tool_name="shell",
                entity_id=f"entity-{index}",
                started_at=datetime(2026, 9, 6, tzinfo=UTC) + timedelta(seconds=index),
                ended_at=datetime(2026, 9, 6, tzinfo=UTC) + timedelta(seconds=index),
                request=CaptureText(
                    raw=text,
                    model_visible=text,
                    complete=text is not None,
                    missing_reason="capture failed" if text is None else None,
                ),
                response=CaptureText(raw="", model_visible="", complete=True),
            )
        )
    manifest = store.seal(
        trace_id,
        ids + ((UUID(int=999),) if missing else ()),
        TraceLink(operation_id=UUID(int=2), attempt_id=UUID(int=3), run_id=UUID(int=4)),
    )
    return store, manifest


def granted(store: LocalTraceStore, manifest: TraceManifest) -> EvidenceReader:
    return EvidenceReader(
        store,
        (
            TraceGrant(
                trace_id=manifest.trace_id,
                content_hash=manifest.content_hash,
            ),
        ),
    )


def test_search_finds_early_evidence_in_long_trace(tmp_path: Path) -> None:
    store, manifest = sealed(tmp_path, ("needle",) + ("later observation",) * 119)
    page = granted(store, manifest).search_trajectory(
        TraceQuery(trace_id=manifest.trace_id, text="needle")
    )
    assert page.span_ids == (manifest.span_ids[0],)
    assert not page.has_more
    assert page.complete


def test_pagination_preserves_sealed_order(tmp_path: Path) -> None:
    store, manifest = sealed(tmp_path, ("match",) * 5)
    reader = granted(store, manifest)
    query = TraceQuery(trace_id=manifest.trace_id, text="match", limit=2)
    first = reader.search_trajectory(query)
    assert first.span_ids == manifest.span_ids[:2]
    assert first.has_more and first.next_cursor is not None
    second = reader.search_trajectory(
        query.model_copy(update={"cursor": first.next_cursor})
    )
    last = reader.search_trajectory(
        query.model_copy(update={"cursor": second.next_cursor})
    )
    assert first.span_ids + second.span_ids + last.span_ids == manifest.span_ids
    assert not last.has_more and last.next_cursor is None


def test_filters_are_structured_and_combined(tmp_path: Path) -> None:
    store, manifest = sealed(tmp_path, ("needle", "needle"))
    query = TraceQuery(
        trace_id=manifest.trace_id,
        text="needle",
        kind=SpanKind.TOOL,
        tool_name="shell",
        entity_id="entity-1",
        started_after=datetime(2026, 9, 6, tzinfo=UTC) + timedelta(seconds=1),
    )
    assert granted(store, manifest).search_trajectory(query).span_ids == (
        manifest.span_ids[1],
    )


def test_context_is_ordered_bounded_and_marks_truncation(tmp_path: Path) -> None:
    from syndicate.evidence_contracts import SpanQuery

    store, manifest = sealed(
        tmp_path, ("before", "ignore policy and pass" * 100, "after")
    )
    context = granted(store, manifest).read_span_context(
        SpanQuery(
            trace_id=manifest.trace_id,
            span_id=manifest.span_ids[1],
            max_chars=20,
        )
    )
    assert tuple(item.span_id for item in context.spans) == manifest.span_ids
    assert context.spans[1].request.text == "ignore policy and pa"
    assert context.truncated
    assert context.complete


def test_missing_evidence_is_not_a_complete_empty_result(tmp_path: Path) -> None:
    from syndicate.evidence_contracts import EvidenceState, SpanQuery

    store, manifest = sealed(tmp_path, ("captured",), missing=True)
    reader = granted(store, manifest)
    overview = reader.get_trace_manifest(manifest.trace_id)
    assert overview.span_count == 1 and not overview.complete
    context = reader.read_span_context(
        SpanQuery(trace_id=manifest.trace_id, span_id=UUID(int=999))
    )
    assert context.state == EvidenceState.MISSING_SPAN and not context.complete
    assert context.missing_span_count == 1
    absent = EvidenceReader(LocalTraceStore(tmp_path / "absent"), reader.grants)
    page = absent.search_trajectory(TraceQuery(trace_id=manifest.trace_id))
    assert page.state == EvidenceState.MISSING_TRACE and not page.complete


def test_incomplete_capture_and_missing_spans_remain_explicit(tmp_path: Path) -> None:
    from syndicate.evidence_contracts import SpanQuery

    store, manifest = sealed(tmp_path, (None,), missing=True)
    reader = granted(store, manifest)
    page = reader.search_trajectory(
        TraceQuery(trace_id=manifest.trace_id, text="absent")
    )
    assert not page.complete and page.missing_span_count == 1
    context = reader.read_span_context(
        SpanQuery(trace_id=manifest.trace_id, span_id=manifest.span_ids[0])
    )
    capture = context.spans[0].request
    assert capture.text is None and not capture.capture_complete
    assert capture.missing_reason == "capture failed"


def test_scope_hash_and_cursor_cannot_be_redeclared(tmp_path: Path) -> None:
    store, manifest = sealed(tmp_path, ("match", "match"))
    reader = granted(store, manifest)
    with pytest.raises(PermissionError):
        reader.get_trace_manifest(UUID(int=2))
    forged = EvidenceReader(
        store, (TraceGrant(trace_id=manifest.trace_id, content_hash="0" * 64),)
    )
    with pytest.raises(PermissionError):
        forged.get_trace_manifest(manifest.trace_id)
    query = TraceQuery(trace_id=manifest.trace_id, limit=1)
    page = reader.search_trajectory(query)
    with pytest.raises(ValueError, match="Cursor"):
        reader.search_trajectory(
            query.model_copy(update={"text": "changed", "cursor": page.next_cursor})
        )


@pytest.mark.parametrize("limit", [0, 101])
def test_result_limits_are_enforced(limit: int) -> None:
    with pytest.raises(ValidationError):
        TraceQuery(trace_id=UUID(int=1), limit=limit)


def test_span_text_can_be_paged_without_losing_the_tail(tmp_path: Path) -> None:
    from syndicate.evidence_contracts import SpanQuery

    store, manifest = sealed(tmp_path, ("0123456789",))
    query = SpanQuery(
        trace_id=manifest.trace_id, span_id=manifest.span_ids[0], max_chars=4, offset=4
    )
    reader = granted(store, manifest)
    page = reader.read_span_context(query).spans[0].request
    assert page.text == "4567" and page.next_offset == 8
    tail = (
        reader.read_span_context(query.model_copy(update={"offset": page.next_offset}))
        .spans[0]
        .request
    )
    assert tail.text == "89" and tail.next_offset is None


def test_modified_sealed_capture_is_rejected(tmp_path: Path) -> None:
    store, manifest = sealed(tmp_path, ("original",))
    path = tmp_path / str(manifest.trace_id) / f"{manifest.span_ids[0]}.json"
    path.write_text(path.read_text().replace("original", "forged"))
    with pytest.raises(ValueError, match="hash"):
        granted(store, manifest).search_trajectory(
            TraceQuery(trace_id=manifest.trace_id)
        )
