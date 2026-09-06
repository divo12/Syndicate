"""Citation contracts: remote Neatlogs IDs are not controller run UUIDs."""

import hashlib
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from syndicate.observability.neatlogs_capture import CaptureReceipt, RunLink


class EvidenceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SpanCitation(EvidenceModel):
    kind: Literal["span"] = "span"
    run_id: UUID
    trace_ref: str = Field(pattern=r"^[0-9a-f]{32}$")
    span_ref: str = Field(pattern=r"^[0-9a-f]{16}$")


class RecordCitation(EvidenceModel):
    kind: Literal["record"] = "record"
    run_id: UUID
    record_ref: str = Field(min_length=1, max_length=200)


Citation = Annotated[SpanCitation | RecordCitation, Field(discriminator="kind")]


class EvidenceStatus(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    MISALIGNED = "misaligned"
    FORBIDDEN = "forbidden"


class EvidenceGrant(EvidenceModel):
    receipt: CaptureReceipt
    semantic_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @property
    def link(self) -> RunLink:
        return self.receipt.link

    @property
    def trace_ref(self) -> str:
        assert self.receipt.trace_ref is not None
        return self.receipt.trace_ref

    @property
    def expected_span_refs(self) -> tuple[str, ...]:
        return self.receipt.expected_span_refs


class RunEvidenceGrant(EvidenceModel):
    """Controller allowlist for one opaque record aligned to a trusted run."""

    operation_id: UUID
    attempt_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1)
    record_ref: str = Field(min_length=1, max_length=200)


class CitationValidation(EvidenceModel):
    status: EvidenceStatus
    complete: bool


class TraceCursor(EvidenceModel):
    semantic_digest: str
    query_hash: str
    offset: int = Field(ge=0)


class TraceQuery(EvidenceModel):
    run_id: UUID
    trace_ref: str = Field(pattern=r"^[0-9a-f]{32}$")
    text: str = Field(default="", max_length=200)
    node_name: str | None = None
    node_type: str | None = None
    limit: int = Field(default=20, ge=1, le=100)
    cursor: TraceCursor | None = None

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(
            self.model_dump_json(exclude={"cursor"}).encode()
        ).hexdigest()


class SearchPage(EvidenceModel):
    status: EvidenceStatus
    complete: bool
    span_refs: tuple[str, ...] = ()
    has_more: bool = False
    truncated: bool = False
    next_cursor: TraceCursor | None = None


class SpanQuery(SpanCitation):
    before: int = Field(default=1, ge=0, le=5)
    after: int = Field(default=1, ge=0, le=5)
    offset: int = Field(default=0, ge=0)
    max_chars: int = Field(default=1000, ge=1, le=2000)


class TextExcerpt(EvidenceModel):
    text: str | None
    next_offset: int | None
    truncated: bool


class SpanExcerpt(EvidenceModel):
    span_ref: str
    input: TextExcerpt
    output: TextExcerpt


class SpanContext(EvidenceModel):
    status: EvidenceStatus
    complete: bool
    spans: tuple[SpanExcerpt, ...] = ()


class ManifestOverview(EvidenceModel):
    status: EvidenceStatus
    complete: bool
    link: RunLink | None = None
    trace_ref: str
    semantic_digest: str | None = None
    span_count: int = 0
