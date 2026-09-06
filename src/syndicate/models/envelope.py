"""Shared workflow wire types; domain results live in referenced artifacts."""

import hashlib
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
        """Hash the canonical typed request, independent of JSON whitespace."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class PreflightCommand(Command):
    operation: Literal[Operation.PREFLIGHT] = Operation.PREFLIGHT
    manifest_hash: Digest


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
    COLLECTION = "collection"
    PROMOTION = "promotion"
    RUNTIME_REQUEST = "runtime_request"
    JUDGE_INPUT = "judge_input"
    PROPOSAL_INPUT = "proposal_input"
    CANDIDATE_RECEIPT = "candidate_receipt"


class ArtifactRef(WireModel):
    kind: ArtifactKind = ArtifactKind.PREFLIGHT
    operation_id: UUID
    attempt_id: UUID
    sha256: Digest


class CommandReceipt(WireModel):
    schema_version: Literal[1] = 1
    operation_id: UUID | None
    attempt_id: UUID | None
    status: CommandStatus
    artifact_refs: tuple[ArtifactRef, ...] = ()
    error: CommandError | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.status == CommandStatus.COMPLETED:
            if self.error is not None or len(self.artifact_refs) != 1:
                raise ValueError(
                    "Completed preflight requires one artifact and no error"
                )
        elif self.error is None or self.artifact_refs:
            raise ValueError("Unsuccessful receipt requires an error and no artifacts")
        return self

    @model_validator(mode="after")
    def validate_ids(self) -> Self:
        if (self.operation_id is None) != (self.attempt_id is None):
            raise ValueError("Receipt IDs must both be present or absent")
        if self.status == CommandStatus.COMPLETED and self.operation_id is None:
            raise ValueError("Completed receipt requires IDs")
        for artifact in self.artifact_refs:
            if (artifact.operation_id, artifact.attempt_id) != (
                self.operation_id,
                self.attempt_id,
            ):
                raise ValueError("Artifact IDs must match receipt IDs")
        return self
