"""Frozen typed contracts for a fresh, isolated paired comparison."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from syndicate.models.budget import BudgetCap
from syndicate.models.candidate import CandidateSeal


class ComparisonObject(BaseModel):
    model_config = ConfigDict(
        frozen=True, strict=True, extra="forbid", arbitrary_types_allowed=True
    )


class Arm(StrEnum):
    INCUMBENT = "incumbent"
    CANDIDATE = "candidate"


class TrialOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCOMPLETE = "incomplete"


class TaskControl(ComparisonObject):
    task_id: str = Field(min_length=1, pattern=r"\S")
    judge_spec_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class PairScheduleRequest(ComparisonObject):
    campaign_id: str = Field(min_length=1, pattern=r"\S")
    incumbent_harness_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    incumbent_memory_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    candidate_memory_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    agent_model_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    runtime_image_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    verifier_version: str = Field(min_length=1, pattern=r"\S")
    seed: int = Field(ge=0)
    arm_budget: BudgetCap
    candidate_seal: CandidateSeal
    task_controls: tuple[TaskControl, ...] = Field(min_length=1)
    repeats: int = Field(ge=1)


class TrialRequest(ComparisonObject):
    trial_id: UUID
    pair_id: UUID
    campaign_id: str
    task_id: str
    repeat_index: int = Field(ge=0)
    arm: Arm
    world_id: UUID
    harness_hash: str
    memory_hash: str
    agent_model_hash: str
    judge_spec_hash: str
    runtime_image_hash: str
    verifier_version: str
    seed: int
    budget: BudgetCap


class PairedTrial(ComparisonObject):
    incumbent: TrialRequest
    candidate: TrialRequest
    first_arm: Arm

    @property
    def ordered_trials(self) -> tuple[TrialRequest, TrialRequest]:
        return (
            (self.incumbent, self.candidate)
            if self.first_arm is Arm.INCUMBENT
            else (self.candidate, self.incumbent)
        )


class PairSchedule(ComparisonObject):
    campaign_id: str
    incumbent_harness_hash: str
    candidate_harness_hash: str
    candidate_diff_hash: str
    pairs: tuple[PairedTrial, ...]

    @property
    def trials(self) -> tuple[TrialRequest, ...]:
        return tuple(trial for pair in self.pairs for trial in pair.ordered_trials)


class ArmExecutionReceipt(ComparisonObject):
    trial_id: UUID
    world_id: UUID
    arm: Arm
    harness_hash: str
    outcome: TrialOutcome
