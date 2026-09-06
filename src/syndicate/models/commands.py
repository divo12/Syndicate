"""Typed controller command requests; schema JSON is deterministic."""

import json
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from syndicate.models.budget import BudgetCap
from syndicate.models.envelope import (
    ArtifactRef,
    Command,
    CommandReceipt,
    Digest,
    Operation,
    PreflightCommand,
)

__all__ = [
    "CollectReportsCommand",
    "Command",
    "CommandRequest",
    "CompareHarnessCommand",
    "JudgeTaskCommand",
    "ProposeHarnessCommand",
    "RunTrialCommand",
    "SelectHarnessCommand",
    "command_schema_json",
    "parse_command",
    "receipt_schema_json",
]


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
    return json.dumps(
        _COMMAND_ADAPTER.json_schema(), sort_keys=True, separators=(",", ":")
    )


def receipt_schema_json() -> str:
    return json.dumps(
        CommandReceipt.model_json_schema(), sort_keys=True, separators=(",", ":")
    )
