"""Typed, frozen inputs and outputs for the predeclared comparison policy."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from syndicate.models.comparison import Arm, TrialOutcome


class SelectionObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class CandidateObjective(StrEnum):
    QUALITY = "quality"
    EFFICIENCY = "efficiency"


class ComparisonDecision(StrEnum):
    PROMOTE = "promote"
    RETAIN = "retain"
    INCONCLUSIVE = "inconclusive"


class ReasonCode(StrEnum):
    EVIDENCE_INCOMPLETE = "evidence-incomplete"
    COST_UNKNOWN = "cost-unknown"
    TASK_REGRESSION_FLOOR = "task-regression-floor"
    SUPPORTED_IMPROVEMENT = "supported-improvement"
    SELECTION_RULE_NOT_MET = "selection-rule-not-met"


class ComparisonPolicy(SelectionObject):
    validation_task_ids: tuple[str, ...] = Field(min_length=1)
    repeats: int = Field(ge=1)
    objective: CandidateObjective
    max_cost_per_success_microusd: int = Field(ge=0)
    max_median_elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def unique_tasks(self) -> "ComparisonPolicy":
        if len(set(self.validation_task_ids)) != len(self.validation_task_ids):
            raise ValueError("Validation task IDs must be unique")
        return self


class TrialMeasurement(SelectionObject):
    trial_id: UUID
    task_id: str = Field(min_length=1, pattern=r"\S")
    arm: Arm
    outcome: TrialOutcome
    verifier_complete: bool
    spend_microusd: int | None = Field(default=None, ge=0)
    elapsed_ms: int = Field(ge=0)


class ArmMetrics(SelectionObject):
    success_rate: float
    reliability: float
    cost_per_success_microusd: float | None
    median_elapsed_ms: float


class ComparisonAssessment(SelectionObject):
    decision: ComparisonDecision
    incumbent: ArmMetrics
    candidate: ArmMetrics
    reason_codes: tuple[ReasonCode, ...]
