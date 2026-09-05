"""Shared workflow wire types; domain results live in referenced artifacts."""

import hashlib
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

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


class PreflightCommand(Command):
    operation: Literal[Operation.PREFLIGHT] = Operation.PREFLIGHT
    manifest_hash: Digest

    @property
    def content_hash(self) -> str:
        """Hash the canonical typed request, independent of JSON whitespace."""
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


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


class ArtifactRef(WireModel):
    kind: Literal["preflight"] = "preflight"
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
