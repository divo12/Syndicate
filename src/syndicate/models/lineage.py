"""Typed accepted-harness versions and promotion receipts."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LineageObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class PromotionStatus(StrEnum):
    PROMOTED = "promoted"
    STALE = "stale"
    ROLLED_BACK = "rolled_back"


class HarnessVersion(LineageObject):
    harness_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    memory_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class PromotionReceipt(LineageObject):
    operation_id: UUID
    status: PromotionStatus
    previous: HarnessVersion
    current: HarnessVersion
