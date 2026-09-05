"""Read-only evidence access within controller-granted trace scopes."""

from uuid import UUID

from syndicate.evidence_contracts import (
    EvidenceState,
    ManifestOverview,
    SearchPage,
    SpanContext,
    SpanExcerpt,
    SpanQuery,
    TextExcerpt,
    TraceCursor,
    TraceGrant,
    TraceQuery,
)
from syndicate.observability.models import CaptureText, TraceManifest, TraceSpan
from syndicate.observability.store import LocalTraceStore


def matches(span: TraceSpan, query: TraceQuery) -> bool:
    if query.started_after and span.started_at < query.started_after:
        return False
    if query.started_before and span.started_at > query.started_before:
        return False
    fields = (
        (query.kind, span.kind),
        (query.status, span.status),
        (query.role, span.role),
        (query.tool_name, span.tool_name),
        (query.entity_id, span.entity_id),
    )
    return all(expected is None or expected == actual for expected, actual in fields)


def excerpt(capture: CaptureText, limit: int, offset: int) -> TextExcerpt:
    text = capture.model_visible
    reason = capture.missing_reason
    end = offset + limit
    return TextExcerpt(
        text=text[offset:end] if text is not None else None,
        next_offset=end if text is not None and len(text) > end else None,
        truncated=offset > 0
        or any(len(value) > limit for value in (text, reason) if value is not None),
        capture_complete=capture.complete,
        missing_reason=reason[:limit] if reason is not None else None,
    )


class EvidenceReader:
    def __init__(self, store: LocalTraceStore, grants: tuple[TraceGrant, ...]) -> None:
        self.store = store
        self.grants = grants

    def _manifest(self, trace_id: UUID) -> TraceManifest | None:
        grant = next((item for item in self.grants if item.trace_id == trace_id), None)
        if grant is None:
            raise PermissionError("Trace is outside the controller grant")
        try:
            manifest = self.store.read_manifest(trace_id)
        except FileNotFoundError:
            return None
        if manifest.content_hash != grant.content_hash:
            raise PermissionError("Trace does not match the approved manifest")
        return manifest

    def search_trajectory(self, query: TraceQuery) -> SearchPage:
        manifest = self._manifest(query.trace_id)
        if manifest is None:
            return SearchPage(
                state=EvidenceState.MISSING_TRACE, span_ids=(), complete=False
            )
        offset = 0
        if query.cursor:
            if (query.cursor.content_hash, query.cursor.query_hash) != (
                manifest.content_hash,
                query.content_hash,
            ):
                raise ValueError("Cursor does not match this trace and query")
            offset = query.cursor.offset
        # ponytail: scan sealed traces per query; index if measured latency requires it.
        matching: list[UUID] = []
        for span_id in manifest.span_ids:
            span = self.store.read(query.trace_id, span_id)
            if not matches(span, query):
                continue
            texts = (span.request.model_visible, span.response.model_visible)
            if any(query.text in text for text in texts if text is not None):
                matching.append(span_id)
        end = offset + query.limit
        has_more = end < len(matching)
        cursor = (
            TraceCursor(
                content_hash=manifest.content_hash,
                query_hash=query.content_hash,
                offset=end,
            )
            if has_more
            else None
        )
        return SearchPage(
            span_ids=tuple(matching[offset:end]),
            complete=manifest.complete,
            has_more=has_more,
            truncated=has_more,
            missing_span_count=len(manifest.missing_span_ids),
            next_cursor=cursor,
        )

    def read_span_context(self, query: SpanQuery) -> SpanContext:
        manifest = self._manifest(query.trace_id)
        if manifest is None:
            return SpanContext(
                state=EvidenceState.MISSING_TRACE,
                spans=(),
                complete=False,
                truncated=False,
            )
        if query.span_id not in manifest.span_ids:
            return SpanContext(
                state=EvidenceState.MISSING_SPAN,
                missing_span_count=len(manifest.missing_span_ids),
                spans=(),
                complete=False,
                truncated=False,
            )
        index = manifest.span_ids.index(query.span_id)
        ids = manifest.span_ids[max(0, index - query.before) : index + query.after + 1]
        spans: list[SpanExcerpt] = []
        for span_id in ids:
            span = self.store.read(query.trace_id, span_id)
            spans.append(
                SpanExcerpt(
                    span_id=span_id,
                    kind=span.kind,
                    status=span.status,
                    started_at=span.started_at,
                    request=excerpt(span.request, query.max_chars, query.offset),
                    response=excerpt(span.response, query.max_chars, query.offset),
                )
            )
        return SpanContext(
            spans=tuple(spans),
            missing_span_count=len(manifest.missing_span_ids),
            complete=manifest.complete,
            truncated=any(
                item.request.truncated or item.response.truncated for item in spans
            ),
        )

    def get_trace_manifest(self, trace_id: UUID) -> ManifestOverview:
        manifest = self._manifest(trace_id)
        if manifest is None:
            return ManifestOverview(
                trace_id=trace_id, state=EvidenceState.MISSING_TRACE
            )
        return ManifestOverview(
            trace_id=trace_id,
            link=manifest.link,
            content_hash=manifest.content_hash,
            span_count=len(manifest.span_ids),
            missing_span_count=len(manifest.missing_span_ids),
            complete=manifest.complete,
        )
