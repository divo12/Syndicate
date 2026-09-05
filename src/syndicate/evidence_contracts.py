"""Citation contracts: remote Neatlogs IDs are not controller run UUIDs."""

from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from syndicate.observability.neatlogs_capture import RunLink


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
    link: RunLink
    trace_ref: str = Field(pattern=r"^[0-9a-f]{32}$")
    semantic_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class CitationValidation(EvidenceModel):
    status: EvidenceStatus
    complete: bool
