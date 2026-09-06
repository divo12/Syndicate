from uuid import UUID

import pytest

from syndicate.budget_policy import BudgetCap
from syndicate.candidate_validation import CandidateSeal
from syndicate.comparison import execute_schedule, schedule_pairs
from syndicate.comparison_contracts import (
    Arm,
    ArmExecutionReceipt,
    PairScheduleRequest,
    TaskControl,
    TrialOutcome,
    TrialRequest,
)


def digest(char: str) -> str:
    return "sha256:" + char * 64


def request() -> PairScheduleRequest:
    return PairScheduleRequest(
        campaign_id="campaign-1",
        incumbent_harness_hash=digest("a"),
        incumbent_memory_hash=digest("b"),
        candidate_memory_hash=digest("c"),
        agent_model_hash=digest("d"),
        runtime_image_hash=digest("e"),
        verifier_version="verifier-1",
        seed=7,
        arm_budget=BudgetCap(max_tokens=100, max_seconds=60, max_spend_microusd=1_000),
        candidate_seal=CandidateSeal(
            parent_hash="a" * 64,
            candidate_hash="f" * 64,
            diff_hash="0" * 64,
            changed_paths=("systemprompt.md",),
        ),
        task_controls=(
            TaskControl(task_id="task-a-1", judge_spec_hash=digest("1")),
            TaskControl(task_id="task-a-2", judge_spec_hash=digest("2")),
        ),
        repeats=2,
    )


def test_schedule_pairs_every_task_repeat_with_equal_controls_and_isolated_worlds() -> (
    None
):
    schedule = schedule_pairs(request())

    assert len(schedule.trials) == 8
    assert tuple(trial.arm for trial in schedule.trials[:4]) == (
        Arm.INCUMBENT,
        Arm.CANDIDATE,
        Arm.CANDIDATE,
        Arm.INCUMBENT,
    )
    assert len({trial.world_id for trial in schedule.trials}) == 8
    for pair in schedule.pairs:
        assert_equal_controls_and_isolation(pair.incumbent, pair.candidate)


def assert_equal_controls_and_isolation(
    incumbent: TrialRequest, candidate: TrialRequest
) -> None:
    assert incumbent.task_id == candidate.task_id
    assert incumbent.repeat_index == candidate.repeat_index
    assert incumbent.agent_model_hash == candidate.agent_model_hash
    assert incumbent.judge_spec_hash == candidate.judge_spec_hash
    assert incumbent.seed == candidate.seed
    assert incumbent.budget == candidate.budget
    assert incumbent.world_id != candidate.world_id
    assert incumbent.harness_hash != candidate.harness_hash


def test_schedule_requires_the_candidate_seal_to_name_the_incumbent() -> None:
    invalid = request().model_copy(
        update={
            "candidate_seal": CandidateSeal(
                parent_hash="0" * 64,
                candidate_hash="f" * 64,
                diff_hash="0" * 64,
                changed_paths=("systemprompt.md",),
            )
        }
    )

    with pytest.raises(ValueError, match="parent"):
        schedule_pairs(invalid)


def test_execution_rejects_a_receipt_that_changes_frozen_controls() -> None:
    schedule = schedule_pairs(request())

    def execute(trial: TrialRequest) -> ArmExecutionReceipt:
        return ArmExecutionReceipt(
            trial_id=trial.trial_id,
            world_id=UUID(int=1),
            arm=trial.arm,
            harness_hash=trial.harness_hash,
            outcome=TrialOutcome.PASSED,
        )

    with pytest.raises(ValueError, match="world"):
        execute_schedule(schedule, execute)
