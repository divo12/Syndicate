"""Shared workflow wire types; domain results live in referenced artifacts."""

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

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
    EXPECTED_REPORTS = "expected_reports"
    POLICY = "policy"
    MEASUREMENTS = "measurements"
    ASSESSMENT = "assessment"
    LINEAGE = "lineage"


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
    expected_reports_ref: ArtifactRef
    report_refs: tuple[ArtifactRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def report_kinds(self) -> Self:
        if self.expected_reports_ref.kind is not ArtifactKind.EXPECTED_REPORTS or any(
            reference.kind is not ArtifactKind.REPORT for reference in self.report_refs
        ):
            raise ValueError("Collection references have invalid artifact kinds")
        return self


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
    policy_ref: ArtifactRef
    measurements_ref: ArtifactRef
    budget: BudgetCap

    @model_validator(mode="after")
    def comparison_kinds(self) -> Self:
        if (
            self.schedule_ref.kind is not ArtifactKind.SCHEDULE
            or self.policy_ref.kind is not ArtifactKind.POLICY
            or self.measurements_ref.kind is not ArtifactKind.MEASUREMENTS
        ):
            raise ValueError("Comparison references have invalid artifact kinds")
        return self


class SelectHarnessCommand(Command):
    operation: Literal[Operation.SELECT_HARNESS] = Operation.SELECT_HARNESS
    parent_harness_hash: Digest
    candidate_harness_hash: Digest
    candidate_memory_hash: Digest
    assessment_ref: ArtifactRef
    lineage_ref: ArtifactRef

    @model_validator(mode="after")
    def selection_kinds(self) -> Self:
        if (
            self.assessment_ref.kind is not ArtifactKind.ASSESSMENT
            or self.lineage_ref.kind is not ArtifactKind.LINEAGE
        ):
            raise ValueError("Selection references have invalid artifact kinds")
        return self


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


class SchemaExportReceipt(WireModel):
    schema_version: Literal[1] = 1
    command_schema_path: str
    command_schema_sha256: Digest
    receipt_schema_path: str
    receipt_schema_sha256: Digest


def command_schema_json() -> str:
    return json.dumps(
        _COMMAND_ADAPTER.json_schema(), sort_keys=True, separators=(",", ":")
    )


def receipt_schema_json() -> str:
    return json.dumps(
        CommandReceipt.model_json_schema(), sort_keys=True, separators=(",", ":")
    )


def schema_artifact_paths(root: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or root.resolve() != root:
        raise ValueError("Schema root must be an absolute nonsymlink path")
    schema_root = root / "schemas"
    return (
        schema_root / "command-request-v1.json",
        schema_root / "command-receipt-v1.json",
    )


def write_schemas(root: Path) -> tuple[Path, Path]:
    paths = schema_artifact_paths(root)
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    for path, payload in zip(
        paths, (command_schema_json(), receipt_schema_json()), strict=True
    ):
        if path.exists() and path.read_text(encoding="utf-8") != payload:
            raise ValueError("Versioned schema artifact differs from this controller")
        if not path.exists():
            path.write_text(payload, encoding="utf-8")
    return paths


def export_schemas(root: Path) -> SchemaExportReceipt:
    paths = write_schemas(root)
    command_payload, receipt_payload = (
        paths[0].read_bytes(),
        paths[1].read_bytes(),
    )
    return SchemaExportReceipt(
        command_schema_path=str(paths[0]),
        command_schema_sha256=hashlib.sha256(command_payload).hexdigest(),
        receipt_schema_path=str(paths[1]),
        receipt_schema_sha256=hashlib.sha256(receipt_payload).hexdigest(),
    )
