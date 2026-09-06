"""Run identity shared by readback consumers; tracing lifecycle lives in tracing.py."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RunLink(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation_id: UUID
    attempt_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1)
