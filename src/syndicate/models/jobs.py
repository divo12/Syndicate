"""Single-tenant optimization job records. Trigger run ids are optional metadata."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

TaskId = Annotated[str, Field(min_length=1, pattern=r"\S")]


class JobObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutorKind(StrEnum):
    SIMULATED = "simulated"
    HARBOR = "harbor"


class StopReason(StrEnum):
    ALL_TASKS_PASSED = "all_tasks_passed"
    MAX_ITERATIONS = "max_iterations"
    NO_IMPROVEMENT = "no_improvement"
    CANCELLED = "cancelled"
    ERROR = "error"


class JobSubmission(JobObject):
    """HTTP boundary: JSON arrays coerce to task_ids."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)
    task_ids: tuple[TaskId, ...] = Field(min_length=1)
    max_iterations: int = Field(default=5, ge=1, le=50)
    patience: int = Field(default=2, ge=1, le=20)
    executor: ExecutorKind = ExecutorKind.SIMULATED

    @model_validator(mode="after")
    def unique_tasks(self) -> Self:
        if len(set(self.task_ids)) != len(self.task_ids):
            raise ValueError("task_ids must be unique")
        return self


class Job(JobObject):
    id: UUID
    status: JobStatus
    task_ids: tuple[TaskId, ...]
    max_iterations: int
    patience: int
    executor: ExecutorKind
    trigger_run_id: str | None = None
    best_score: float | None = None
    stop_reason: StopReason | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INFRA_ERROR = "infra_error"


class IterationPhase(StrEnum):
    BENCHMARK = "benchmark"
    IMPROVE = "improve"
    DONE = "done"


class TaskResult(JobObject):
    task_id: TaskId
    outcome: TaskOutcome
    reward: float = Field(ge=0, le=1)


class Iteration(JobObject):
    job_id: UUID
    number: int = Field(ge=0)
    phase: IterationPhase
    generation: int = Field(ge=0)
    score: float | None = None
    accepted: bool | None = None
    results: tuple[TaskResult, ...] = ()
