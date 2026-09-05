"""Typed local evidence records; captured text is data, never instructions."""

from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from syndicate.budget_policy import ProductRole


class TraceRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SpanKind(StrEnum):
    AGENT = "agent"
    MODEL = "model"
    TOOL = "tool"


class SpanStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    CANCELLED = "cancelled"


class CaptureText(TraceRecord):
    raw: str | None
    model_visible: str | None
    complete: bool
    missing_reason: str | None = None

    @model_validator(mode="after")
    def validate_completeness(self) -> Self:
        if self.complete and (self.raw is None or self.model_visible is None):
            raise ValueError("Complete capture requires raw and model-visible text")
        if not self.complete and self.missing_reason is None:
            raise ValueError("Incomplete capture requires a reason")
        return self


class TraceSpan(TraceRecord):
    trace_id: UUID
    span_id: UUID
    parent_span_id: UUID | None
    role: ProductRole
    kind: SpanKind
    status: SpanStatus
    tool_name: str | None = Field(default=None, min_length=1)
    entity_id: str | None = Field(default=None, min_length=1)
    started_at: AwareDatetime
    ended_at: AwareDatetime
    request: CaptureText
    response: CaptureText

    @model_validator(mode="after")
    def validate_duration(self) -> Self:
        if self.ended_at < self.started_at:
            raise ValueError("Span ends before it starts")
        if self.parent_span_id == self.span_id:
            raise ValueError("Span cannot parent itself")
        return self


class TraceManifest(TraceRecord):
    trace_id: UUID
    span_ids: tuple[UUID, ...]
    content_hash: str
    complete: bool
    missing_span_ids: tuple[UUID, ...]
    missing_reasons: tuple[str, ...]
    sealed_at: AwareDatetime
