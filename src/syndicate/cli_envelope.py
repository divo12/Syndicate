"""Shared workflow wire types; domain results live in referenced artifacts."""

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from syndicate.budget_policy import BudgetCap

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class WireModel(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )


class Operation(StrEnum):
    PREFLIGHT = "preflight"
    RUN_TRIAL = "run-trial"
    JUDGE_TASK = "judge-task"
    COLLECT_REPORTS = "collect-reports"
    PROPOSE_HARNESS = "propose-harness"
    COMPARE_HARNESS = "compare-harness"
    SELECT_HARNESS = "select-harness"
    REPORT = "report"


class Command(WireModel):
    schema_version: Literal[1] = 1
    operation_id: UUID
    attempt_id: UUID
    operation: Operation

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class PreflightCommand(Command):
    operation: Literal[Operation.PREFLIGHT] = Operation.PREFLIGHT
    manifest_hash: Digest


class ArtifactKind(StrEnum):
    PREFLIGHT = "preflight"
    RUN = "run"
    REPORT = "report"
    DIAGNOSIS = "diagnosis"
    SCHEDULE = "schedule"
    COMPARISON = "comparison"


class ArtifactRef(WireModel):
    kind: ArtifactKind
    operation_id: UUID
    attempt_id: UUID
    sha256: Digest


class RunTrialCommand(Command):
    operation: Literal[Operation.RUN_TRIAL] = Operation.RUN_TRIAL
    task_id: str = Field(min_length=1, pattern=r"\S")
    harness_hash: Digest
    memory_hash: Digest
    model_config_hash: Digest
    runtime_image_hash: Digest
    judge_spec_hash: Digest
    verifier_version: str = Field(min_length=1, pattern=r"\S")
    budget: BudgetCap


class JudgeTaskCommand(Command):
    operation: Literal[Operation.JUDGE_TASK] = Operation.JUDGE_TASK
    task_id: str = Field(min_length=1, pattern=r"\S")
    judge_spec_hash: Digest
    run_refs: tuple[ArtifactRef, ...] = Field(min_length=1)
    budget: BudgetCap


class CollectReportsCommand(Command):
    operation: Literal[Operation.COLLECT_REPORTS] = Operation.COLLECT_REPORTS
    expected_task_ids: tuple[str, ...] = Field(min_length=1)
    report_refs: tuple[ArtifactRef, ...] = Field(min_length=1)


class ProposeHarnessCommand(Command):
    operation: Literal[Operation.PROPOSE_HARNESS] = Operation.PROPOSE_HARNESS
    candidate_id: str = Field(min_length=1, pattern=r"\S")
    parent_harness_hash: Digest
    diagnosis_ref: ArtifactRef
    budget: BudgetCap


class CompareHarnessCommand(Command):
    operation: Literal[Operation.COMPARE_HARNESS] = Operation.COMPARE_HARNESS
    parent_harness_hash: Digest
    candidate_harness_hash: Digest
    schedule_ref: ArtifactRef
    budget: BudgetCap


class SelectHarnessCommand(Command):
    operation: Literal[Operation.SELECT_HARNESS] = Operation.SELECT_HARNESS
    parent_harness_hash: Digest
    candidate_harness_hash: Digest
    comparison_ref: ArtifactRef


type CommandRequest = Annotated[
    PreflightCommand
    | RunTrialCommand
    | JudgeTaskCommand
    | CollectReportsCommand
    | ProposeHarnessCommand
    | CompareHarnessCommand
    | SelectHarnessCommand,
    Field(discriminator="operation"),
]
_COMMAND_ADAPTER: TypeAdapter[CommandRequest] = TypeAdapter(CommandRequest)


def parse_command(payload: str | bytes) -> CommandRequest:
    """External JSON boundary; internal code receives a discriminated model."""
    return _COMMAND_ADAPTER.validate_json(payload)


def command_schema_json() -> str:
    """Stable code-generation input; callers write it as a controller artifact."""
    return json.dumps(
        _COMMAND_ADAPTER.json_schema(), sort_keys=True, separators=(",", ":")
    )


class CommandStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class ErrorReason(StrEnum):
    INVALID_REQUEST = "invalid_request"
    INVALID_CONFIGURATION = "invalid_configuration"
    INFRASTRUCTURE = "infrastructure"


class CommandError(WireModel):
    reason: ErrorReason
    message: str


class CommandReceipt(WireModel):
    schema_version: Literal[1] = 1
    operation_id: UUID | None
    attempt_id: UUID | None
    status: CommandStatus
    artifact_refs: tuple[ArtifactRef, ...] = ()
    error: CommandError | None = None
