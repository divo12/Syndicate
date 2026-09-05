"""Bounded query inputs; controller grants never come from caller requests."""

import hashlib
from enum import StrEnum
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from syndicate.budget_policy import ProductRole
from syndicate.observability.models import SpanKind, SpanStatus, TraceLink


class EvidenceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class EvidenceState(StrEnum):
    AVAILABLE = "available"
    MISSING_TRACE = "missing_trace"
    MISSING_SPAN = "missing_span"


class ManifestOverview(EvidenceModel):
    trace_id: UUID
    link: TraceLink | None = None
    state: EvidenceState = EvidenceState.AVAILABLE
    content_hash: str | None = None
    span_count: int = 0
    missing_span_count: int = 0
    complete: bool = False


class TraceGrant(EvidenceModel):
    trace_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class TraceCursor(EvidenceModel):
    content_hash: str
    query_hash: str
    offset: int = Field(ge=0)


class TraceQuery(EvidenceModel):
    trace_id: UUID
    text: str = Field(default="", max_length=200)
    limit: int = Field(default=20, ge=1, le=100)
    cursor: TraceCursor | None = None
    kind: SpanKind | None = None
    status: SpanStatus | None = None
    role: ProductRole | None = None
    tool_name: str | None = Field(default=None, max_length=200)
    entity_id: str | None = Field(default=None, max_length=200)
    started_after: AwareDatetime | None = None
    started_before: AwareDatetime | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(
            self.model_dump_json(exclude={"cursor"}).encode()
        ).hexdigest()


class SearchPage(EvidenceModel):
    state: EvidenceState = EvidenceState.AVAILABLE
    missing_span_count: int = 0
    truncated: bool = False
    span_ids: tuple[UUID, ...]
    has_more: bool = False
    complete: bool
    next_cursor: TraceCursor | None = None


class SpanQuery(EvidenceModel):
    trace_id: UUID
    span_id: UUID
    before: int = Field(default=1, ge=0, le=5)
    after: int = Field(default=1, ge=0, le=5)
    max_chars: int = Field(default=1000, ge=1, le=2000)
    offset: int = Field(default=0, ge=0)


class TextExcerpt(EvidenceModel):
    next_offset: int | None
    text: str | None
    truncated: bool
    capture_complete: bool
    missing_reason: str | None


class SpanExcerpt(EvidenceModel):
    span_id: UUID
    kind: SpanKind
    status: SpanStatus
    started_at: AwareDatetime
    request: TextExcerpt
    response: TextExcerpt


class SpanContext(EvidenceModel):
    state: EvidenceState = EvidenceState.AVAILABLE
    missing_span_count: int = 0
    spans: tuple[SpanExcerpt, ...]
    complete: bool
    truncated: bool
