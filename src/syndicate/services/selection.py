"""Calculate conservative paired metrics and apply the frozen selection rule."""

from statistics import median

from syndicate.models.comparison import Arm, TrialOutcome
from syndicate.models.selection import (
    ArmMetrics,
    CandidateObjective,
    ComparisonAssessment,
    ComparisonDecision,
    ComparisonPolicy,
    ReasonCode,
    TrialMeasurement,
)


def assess_comparison(
    policy: ComparisonPolicy, measurements: tuple[TrialMeasurement, ...]
) -> ComparisonAssessment:
    """Retain on a floor violation and abstain on missing evidence or pricing."""
    incumbent = _metrics(measurements, Arm.INCUMBENT)
    candidate = _metrics(measurements, Arm.CANDIDATE)
    if not _complete(policy, measurements):
        return _assessment(
            ComparisonDecision.INCONCLUSIVE,
            incumbent,
            candidate,
            ReasonCode.EVIDENCE_INCOMPLETE,
        )
    if (
        incumbent.cost_per_success_microusd is None
        or candidate.cost_per_success_microusd is None
    ):
        return _assessment(
            ComparisonDecision.INCONCLUSIVE,
            incumbent,
            candidate,
            ReasonCode.COST_UNKNOWN,
        )
    if _regressed(policy, measurements):
        return _assessment(
            ComparisonDecision.RETAIN,
            incumbent,
            candidate,
            ReasonCode.TASK_REGRESSION_FLOOR,
        )
    if _promotable(policy, incumbent, candidate):
        return _assessment(
            ComparisonDecision.PROMOTE,
            incumbent,
            candidate,
            ReasonCode.SUPPORTED_IMPROVEMENT,
        )
    return _assessment(
        ComparisonDecision.RETAIN,
        incumbent,
        candidate,
        ReasonCode.SELECTION_RULE_NOT_MET,
    )


def _metrics(measurements: tuple[TrialMeasurement, ...], arm: Arm) -> ArmMetrics:
    arm_trials = tuple(item for item in measurements if item.arm is arm)
    successes = tuple(
        item for item in arm_trials if item.outcome is TrialOutcome.PASSED
    )
    return ArmMetrics(
        success_rate=len(successes) / len(arm_trials) if arm_trials else 0.0,
        reliability=_reliability(arm_trials),
        cost_per_success_microusd=_cost_per_success(arm_trials, successes),
        median_elapsed_ms=median(item.elapsed_ms for item in arm_trials)
        if arm_trials
        else 0.0,
    )


def _reliability(measurements: tuple[TrialMeasurement, ...]) -> float:
    task_ids = tuple(sorted({item.task_id for item in measurements}))
    passed_every_repeat = sum(
        all(
            item.outcome is TrialOutcome.PASSED
            for item in measurements
            if item.task_id == task_id
        )
        for task_id in task_ids
    )
    return passed_every_repeat / len(task_ids) if task_ids else 0.0


def _cost_per_success(
    measurements: tuple[TrialMeasurement, ...], successes: tuple[TrialMeasurement, ...]
) -> float | None:
    spend = tuple(item.spend_microusd for item in measurements)
    if not successes or any(value is None for value in spend):
        return None
    return sum(value for value in spend if value is not None) / len(successes)


def _complete(
    policy: ComparisonPolicy, measurements: tuple[TrialMeasurement, ...]
) -> bool:
    if any(not item.verifier_complete for item in measurements):
        return False
    for task_id in policy.validation_task_ids:
        for arm in Arm:
            if (
                sum(
                    item.task_id == task_id and item.arm is arm for item in measurements
                )
                != policy.repeats
            ):
                return False
    return True


def _regressed(
    policy: ComparisonPolicy, measurements: tuple[TrialMeasurement, ...]
) -> bool:
    for task_id in policy.validation_task_ids:
        incumbent = sum(
            item.task_id == task_id
            and item.arm is Arm.INCUMBENT
            and item.outcome is TrialOutcome.PASSED
            for item in measurements
        )
        candidate = sum(
            item.task_id == task_id
            and item.arm is Arm.CANDIDATE
            and item.outcome is TrialOutcome.PASSED
            for item in measurements
        )
        if candidate < incumbent:
            return True
    return False


def _promotable(
    policy: ComparisonPolicy, incumbent: ArmMetrics, candidate: ArmMetrics
) -> bool:
    if candidate.median_elapsed_ms > policy.max_median_elapsed_ms:
        return False
    if candidate.cost_per_success_microusd is None:
        return False
    if candidate.cost_per_success_microusd > policy.max_cost_per_success_microusd:
        return False
    if policy.objective is CandidateObjective.QUALITY:
        return candidate.success_rate > incumbent.success_rate
    if incumbent.cost_per_success_microusd is None:
        return False
    return (
        candidate.cost_per_success_microusd <= incumbent.cost_per_success_microusd * 0.9
        and candidate.median_elapsed_ms <= incumbent.median_elapsed_ms * 1.1
    )


def _assessment(
    decision: ComparisonDecision,
    incumbent: ArmMetrics,
    candidate: ArmMetrics,
    reason: ReasonCode,
) -> ComparisonAssessment:
    return ComparisonAssessment(
        decision=decision,
        incumbent=incumbent,
        candidate=candidate,
        reason_codes=(reason,),
    )
