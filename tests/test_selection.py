from uuid import UUID

from syndicate.models.comparison import Arm, TrialOutcome
from syndicate.models.selection import (
    CandidateObjective,
    ComparisonDecision,
    ComparisonPolicy,
    TrialMeasurement,
)
from syndicate.services.selection import assess_comparison


def measurement(
    trial: int,
    arm: Arm,
    task_id: str,
    passed: bool,
    complete: bool = True,
    spend_microusd: int | None = 100,
) -> TrialMeasurement:
    return TrialMeasurement(
        trial_id=UUID(int=trial),
        task_id=task_id,
        arm=arm,
        outcome=TrialOutcome.PASSED if passed else TrialOutcome.FAILED,
        verifier_complete=complete,
        spend_microusd=spend_microusd,
        elapsed_ms=100,
    )


def policy(
    objective: CandidateObjective = CandidateObjective.QUALITY,
) -> ComparisonPolicy:
    return ComparisonPolicy(
        validation_task_ids=("task-a-1", "task-a-2"),
        repeats=2,
        objective=objective,
        max_cost_per_success_microusd=200,
        max_median_elapsed_ms=200,
    )


def test_quality_promotion_requires_higher_accuracy_without_task_regression() -> None:
    results = (
        measurement(1, Arm.INCUMBENT, "task-a-1", True),
        measurement(2, Arm.INCUMBENT, "task-a-1", False),
        measurement(3, Arm.CANDIDATE, "task-a-1", True),
        measurement(4, Arm.CANDIDATE, "task-a-1", True),
        measurement(5, Arm.INCUMBENT, "task-a-2", False),
        measurement(6, Arm.INCUMBENT, "task-a-2", False),
        measurement(7, Arm.CANDIDATE, "task-a-2", False),
        measurement(8, Arm.CANDIDATE, "task-a-2", False),
    )

    assessment = assess_comparison(policy(), results)

    assert assessment.decision is ComparisonDecision.PROMOTE
    assert assessment.candidate.reliability == 0.5


def test_task_regression_retains_even_when_aggregate_accuracy_rises() -> None:
    results = (
        measurement(1, Arm.INCUMBENT, "task-a-1", True),
        measurement(2, Arm.INCUMBENT, "task-a-1", True),
        measurement(3, Arm.CANDIDATE, "task-a-1", True),
        measurement(4, Arm.CANDIDATE, "task-a-1", False),
        measurement(5, Arm.INCUMBENT, "task-a-2", False),
        measurement(6, Arm.INCUMBENT, "task-a-2", False),
        measurement(7, Arm.CANDIDATE, "task-a-2", True),
        measurement(8, Arm.CANDIDATE, "task-a-2", True),
    )

    assert assess_comparison(policy(), results).decision is ComparisonDecision.RETAIN


def test_missing_verifier_or_pricing_is_inconclusive_without_a_fake_zero_cost() -> None:
    results = (
        measurement(
            1, Arm.INCUMBENT, "task-a-1", True, complete=False, spend_microusd=None
        ),
        measurement(2, Arm.CANDIDATE, "task-a-1", True),
    )

    assessment = assess_comparison(policy(), results)

    assert assessment.decision is ComparisonDecision.INCONCLUSIVE
    assert assessment.incumbent.cost_per_success_microusd is None
