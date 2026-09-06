"""Offline composition check for M4's schedule, accounting, selection and lineage."""

from pathlib import Path
from uuid import UUID

from syndicate.models.budget import (
    BudgetCap,
    CampaignBudgetPolicy,
    ProductRole,
    RoleBudget,
)
from syndicate.models.candidate import CandidateSeal
from syndicate.models.comparison import (
    Arm,
    PairScheduleRequest,
    TaskControl,
    TrialOutcome,
)
from syndicate.models.lineage import PromotionStatus
from syndicate.models.selection import (
    CandidateObjective,
    ComparisonDecision,
    ComparisonPolicy,
    TrialMeasurement,
)
from syndicate.services.comparison import schedule_pairs
from syndicate.services.lineage import HarnessLineage
from syndicate.services.reservations import ReservationLedger
from syndicate.services.selection import assess_comparison


def digest(char: str) -> str:
    return "sha256:" + char * 64


def cap(spend: int = 1_000_000) -> BudgetCap:
    return BudgetCap(max_tokens=100, max_seconds=60, max_spend_microusd=spend)


def test_offline_paired_controller_promotes_only_after_complete_supported_measurements(
    tmp_path: Path,
) -> None:
    budget = cap()
    request = PairScheduleRequest(
        campaign_id="campaign-1",
        incumbent_harness_hash=digest("a"),
        incumbent_memory_hash=digest("b"),
        candidate_memory_hash=digest("c"),
        agent_model_hash=digest("d"),
        runtime_image_hash=digest("e"),
        verifier_version="verifier-1",
        seed=1,
        arm_budget=budget,
        candidate_seal=CandidateSeal("a" * 64, "f" * 64, "0" * 64, ("prompt",)),
        task_controls=(TaskControl(task_id="task-a-1", judge_spec_hash=digest("1")),),
        repeats=2,
    )
    schedule = schedule_pairs(request)
    policy = CampaignBudgetPolicy(
        role_budgets=tuple(
            RoleBudget(role=role, cap=cap(4_000_000)) for role in ProductRole
        ),
        campaign_cap=cap(4_000_000),
    )
    ledger = ReservationLedger(tmp_path / "controller.sqlite", policy)
    measurements = tuple(
        TrialMeasurement(
            trial_id=trial.trial_id,
            task_id=trial.task_id,
            arm=trial.arm,
            outcome=(
                TrialOutcome.PASSED
                if trial.arm is Arm.CANDIDATE or trial.repeat_index == 0
                else TrialOutcome.FAILED
            ),
            verifier_complete=True,
            spend_microusd=100,
            elapsed_ms=50,
        )
        for trial in schedule.trials
    )
    for trial in schedule.trials:
        assert ledger.reserve(ProductRole.EXECUTOR, trial.trial_id, budget)
    assessment = assess_comparison(
        ComparisonPolicy(
            validation_task_ids=("task-a-1",),
            repeats=2,
            objective=CandidateObjective.QUALITY,
            max_cost_per_success_microusd=200,
            max_median_elapsed_ms=100,
        ),
        measurements,
    )
    lineage = HarnessLineage(tmp_path / "lineage.sqlite", digest("a"), digest("b"))

    assert assessment.decision is ComparisonDecision.PROMOTE
    assert (
        lineage.promote(UUID(int=1), digest("a"), digest("f"), digest("c")).status
        is PromotionStatus.PROMOTED
    )
