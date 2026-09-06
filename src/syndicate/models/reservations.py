"""Typed, frozen usage reservations for controller-owned model work."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from syndicate.models.budget import BudgetCap, ProductRole


class ReservationObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ReservationState(StrEnum):
    RESERVED = "reserved"
    RECONCILED = "reconciled"
    CANCELLED = "cancelled"


class Usage(ReservationObject):
    tokens: int = Field(ge=0)
    seconds: int = Field(ge=0)
    spend_microusd: int = Field(ge=0)


class UsageReservation(ReservationObject):
    operation_id: UUID
    role: ProductRole
    cap: BudgetCap
    state: ReservationState
    retry_of: UUID | None = None
    usage: Usage | None = None
