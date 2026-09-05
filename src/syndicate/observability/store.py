"""Filesystem-backed immutable local trace capture."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from .models import TraceManifest, TraceSpan


class LocalTraceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def record(self, span: TraceSpan) -> None:
        trace = self._trace_dir(span.trace_id)
        if (trace / "manifest.json").exists():
            raise ValueError("Trace is sealed")
        trace.mkdir(parents=True, exist_ok=True)
        with (trace / f"{span.span_id}.json").open("x", encoding="utf-8") as file:
            file.write(span.model_dump_json())

    def read(self, trace_id: UUID, span_id: UUID) -> TraceSpan:
        return TraceSpan.model_validate_json(
            (self._trace_dir(trace_id) / f"{span_id}.json").read_bytes()
        )

    def read_manifest(self, trace_id: UUID) -> TraceManifest:
        manifest = TraceManifest.model_validate_json(
            (self._trace_dir(trace_id) / "manifest.json").read_bytes()
        )
        try:
            spans = tuple(self.read(trace_id, span_id) for span_id in manifest.span_ids)
        except OSError:
            raise ValueError("Sealed trace is missing a span") from None
        if self._content_hash(spans) != manifest.content_hash:
            raise ValueError("Sealed trace content hash mismatch")
        return manifest

    def seal(
        self, trace_id: UUID, expected_span_ids: tuple[UUID, ...]
    ) -> TraceManifest:
        trace = self._trace_dir(trace_id)
        trace.mkdir(parents=True, exist_ok=True)
        spans = tuple(
            self.read(trace_id, span_id)
            for span_id in expected_span_ids
            if (trace / f"{span_id}.json").is_file()
        )
        missing = tuple(
            span_id
            for span_id in expected_span_ids
            if not (trace / f"{span_id}.json").is_file()
        )
        reasons = tuple(f"missing-span:{span_id}" for span_id in missing) + tuple(
            f"incomplete-span:{span.span_id}"
            for span in spans
            if not span.request.complete or not span.response.complete
        )
        manifest = TraceManifest(
            trace_id=trace_id,
            span_ids=tuple(span.span_id for span in spans),
            content_hash=self._content_hash(spans),
            complete=not reasons,
            missing_span_ids=missing,
            missing_reasons=reasons,
            sealed_at=datetime.now(UTC),
        )
        with (trace / "manifest.json").open("x", encoding="utf-8") as file:
            file.write(manifest.model_dump_json())
        return manifest

    def _trace_dir(self, trace_id: UUID) -> Path:
        return self.root / str(trace_id)

    def _content_hash(self, spans: tuple[TraceSpan, ...]) -> str:
        content = "\n".join(span.model_dump_json() for span in spans).encode()
        return hashlib.sha256(content).hexdigest()
