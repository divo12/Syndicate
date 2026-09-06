"""Outer accept/stop loop. Uses assess_comparison and lineage; improve is injected."""

from collections.abc import Callable
from uuid import UUID, uuid4

from syndicate.models.comparison import Arm, TrialOutcome
from syndicate.models.jobs import (
    Iteration,
    IterationPhase,
    Job,
    StopReason,
    TaskOutcome,
    TaskResult,
)
from syndicate.models.lineage import PromotionStatus
from syndicate.models.selection import (
    CandidateObjective,
    ComparisonDecision,
    ComparisonPolicy,
    ReasonCode,
    TrialMeasurement,
)
from syndicate.services.executors import SimulatedExecutor, score_of
from syndicate.services.lineage import HarnessLineage
from syndicate.services.selection import assess_comparison

Improve = Callable[[int], int]
Executor = Callable[[tuple[str, ...], int], tuple[TaskResult, ...]]


class LoopReceipt:
    def __init__(
        self,
        iterations: tuple[Iteration, ...],
        stop_reason: StopReason,
        best_score: float,
    ) -> None:
        self.iterations = iterations
        self.stop_reason = stop_reason
        self.best_score = best_score


def _measurements(
    results: tuple[TaskResult, ...], arm: Arm, start: int
) -> tuple[TrialMeasurement, ...]:
    rows: list[TrialMeasurement] = []
    for offset, result in enumerate(results):
        rows.append(
            TrialMeasurement(
                trial_id=UUID(int=start + offset),
                task_id=result.task_id,
                arm=arm,
                outcome=_trial_outcome(result.outcome),
                verifier_complete=result.outcome is not TaskOutcome.INFRA_ERROR,
                spend_microusd=None,
                elapsed_ms=0,
            )
        )
    return tuple(rows)


def _trial_outcome(outcome: TaskOutcome) -> TrialOutcome:
    if outcome is TaskOutcome.PASSED:
        return TrialOutcome.PASSED
    if outcome is TaskOutcome.INFRA_ERROR:
        return TrialOutcome.INCOMPLETE
    return TrialOutcome.FAILED


def _policy(task_ids: tuple[str, ...]) -> ComparisonPolicy:
    return ComparisonPolicy(
        validation_task_ids=task_ids,
        repeats=1,
        objective=CandidateObjective.QUALITY,
        max_cost_per_success_microusd=10_000,
        max_median_elapsed_ms=10_000,
    )


def _decide_stop(
    score: float, stagnant: int, iteration: int, job: Job, accepted: bool
) -> StopReason | None:
    if accepted and score == 1:
        return StopReason.ALL_TASKS_PASSED
    if stagnant >= job.patience:
        return StopReason.NO_IMPROVEMENT
    if iteration + 1 >= job.max_iterations:
        return StopReason.MAX_ITERATIONS
    return None


def _select(
    incumbent: tuple[TaskResult, ...],
    candidate: tuple[TaskResult, ...],
    job: Job,
    lineage: HarnessLineage | None,
    parent_generation: int,
    child_generation: int,
) -> bool:
    assessment = assess_comparison(
        _policy(job.task_ids),
        _measurements(incumbent, Arm.INCUMBENT, 1)
        + _measurements(candidate, Arm.CANDIDATE, 1 + len(incumbent)),
    )
    improved = score_of(candidate) > score_of(incumbent)
    inconclusive_cost = (
        assessment.decision is ComparisonDecision.INCONCLUSIVE
        and ReasonCode.COST_UNKNOWN in assessment.reason_codes
        and improved
    )
    if assessment.decision is not ComparisonDecision.PROMOTE and not inconclusive_cost:
        return False
    if lineage is None:
        return True
    parent = f"sha256:{parent_generation:064x}"
    child = f"sha256:{child_generation:064x}"
    receipt = lineage.promote(uuid4(), parent, child, child)
    return receipt.status is PromotionStatus.PROMOTED


def run_outer_loop(
    job: Job,
    executor: Executor | None = None,
    improve: Improve | None = None,
    lineage: HarnessLineage | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> LoopReceipt:
    run = executor or SimulatedExecutor().run
    bump = improve or (lambda generation: generation + 1)
    iterations: list[Iteration] = []
    generation = 0
    incumbent_generation = 0
    best = -1.0
    stagnant = 0
    baseline: tuple[TaskResult, ...] | None = None
    for number in range(job.max_iterations):
        if cancelled is not None and cancelled():
            return LoopReceipt(tuple(iterations), StopReason.CANCELLED, max(best, 0.0))
        results = run(job.task_ids, generation)
        score = score_of(results)
        accepted = score > best
        if accepted and baseline is not None:
            accepted = _select(
                baseline, results, job, lineage, incumbent_generation, generation
            )
        if accepted:
            best = score
            stagnant = 0
            baseline = results
            incumbent_generation = generation
        else:
            stagnant += 1
        stop = _decide_stop(score, stagnant, number, job, accepted)
        iterations.append(
            Iteration(
                job_id=job.id,
                number=number,
                phase=IterationPhase.DONE,
                generation=generation,
                score=score,
                accepted=accepted,
                results=results,
            )
        )
        if stop is not None:
            return LoopReceipt(tuple(iterations), stop, max(best, 0.0))
        generation = bump(generation)
    return LoopReceipt(tuple(iterations), StopReason.MAX_ITERATIONS, max(best, 0.0))
