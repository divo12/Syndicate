from uuid import UUID

import pytest
from pydantic import ValidationError

from syndicate.budget_policy import BudgetCap
from syndicate.cli_envelope import (
    ArtifactKind,
    CollectReportsCommand,
    Command,
    CompareHarnessCommand,
    JudgeTaskCommand,
    ProposeHarnessCommand,
    RunTrialCommand,
    SelectHarnessCommand,
    command_schema_json,
    parse_command,
)

BUDGET = '"budget":{"max_tokens":10,"max_seconds":10,"max_spend_microusd":10}'


def digest(char: str) -> str:
    return char * 64


def cap() -> BudgetCap:
    return BudgetCap(max_tokens=10, max_seconds=10, max_spend_microusd=10)


@pytest.mark.parametrize(
    ("command_type", "operation", "extra"),
    (
        (
            RunTrialCommand,
            "run-trial",
            '"task_id":"task-a-1","harness_hash":"'
            + digest("a")
            + '","memory_hash":"'
            + digest("b")
            + '","model_config_hash":"'
            + digest("c")
            + '","runtime_image_hash":"'
            + digest("d")
            + '","judge_spec_hash":"'
            + digest("e")
            + '","verifier_version":"v1",'
            + BUDGET,
        ),
        (
            JudgeTaskCommand,
            "judge-task",
            '"task_id":"task-a-1","judge_spec_hash":"'
            + digest("a")
            + '","run_refs":[{"kind":"run","operation_id":"'
            + str(UUID(int=3))
            + '","attempt_id":"'
            + str(UUID(int=4))
            + '","sha256":"'
            + digest("b")
            + '"}],"budget":{"max_tokens":10,"max_seconds":10,"max_spend_microusd":10}',
        ),
        (
            CollectReportsCommand,
            "collect-reports",
            '"expected_task_ids":["task-a-1"],"report_refs":[{"kind":"report","operation_id":"'
            + str(UUID(int=3))
            + '","attempt_id":"'
            + str(UUID(int=4))
            + '","sha256":"'
            + digest("a")
            + '"}]',
        ),
        (
            ProposeHarnessCommand,
            "propose-harness",
            '"candidate_id":"candidate-1","parent_harness_hash":"'
            + digest("a")
            + '","diagnosis_ref":{"kind":"diagnosis","operation_id":"'
            + str(UUID(int=3))
            + '","attempt_id":"'
            + str(UUID(int=4))
            + '","sha256":"'
            + digest("b")
            + '"},"budget":{"max_tokens":10,"max_seconds":10,"max_spend_microusd":10}',
        ),
        (
            CompareHarnessCommand,
            "compare-harness",
            '"parent_harness_hash":"'
            + digest("a")
            + '","candidate_harness_hash":"'
            + digest("b")
            + '","schedule_ref":{"kind":"schedule","operation_id":"'
            + str(UUID(int=3))
            + '","attempt_id":"'
            + str(UUID(int=4))
            + '","sha256":"'
            + digest("c")
            + '"},"budget":{"max_tokens":10,"max_seconds":10,"max_spend_microusd":10}',
        ),
        (
            SelectHarnessCommand,
            "select-harness",
            '"parent_harness_hash":"'
            + digest("a")
            + '","candidate_harness_hash":"'
            + digest("b")
            + '","comparison_ref":{"kind":"comparison","operation_id":"'
            + str(UUID(int=3))
            + '","attempt_id":"'
            + str(UUID(int=4))
            + '","sha256":"'
            + digest("c")
            + '"}',
        ),
    ),
)
def test_each_controller_operation_parses_to_its_strict_typed_command(
    command_type: type[Command], operation: str, extra: str
) -> None:
    raw = (
        '{"schema_version":1,"operation_id":"'
        + str(UUID(int=1))
        + '","attempt_id":"'
        + str(UUID(int=2))
        + '","operation":"'
        + operation
        + '",'
        + extra
        + "}"
    )
    assert isinstance(parse_command(raw), command_type)


def test_schema_is_deterministic_and_artifact_kinds_are_closed() -> None:
    assert command_schema_json() == command_schema_json()
    assert ArtifactKind.COMPARISON.value == "comparison"
    with pytest.raises(ValidationError):
        parse_command('{"operation":"run-trial"}')
