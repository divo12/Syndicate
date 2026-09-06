"""Task executors used by the job worker. Harbor binds to live handlers."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from syndicate.models.jobs import ExecutorKind, TaskOutcome, TaskResult

if TYPE_CHECKING:
    from syndicate.services.benchmark import VerifierReceipt

HarborTask = Callable[[str, int], TaskResult]


class SimulatedExecutor:
    """Deterministic stand-in: generation 0 fails the last task, later gens pass."""

    kind = ExecutorKind.SIMULATED

    def run(
        self,
        task_ids: tuple[str, ...],
        generation: int,
        failing_task_id: str | None = None,
    ) -> tuple[TaskResult, ...]:
        if not task_ids:
            raise ValueError("task_ids must not be empty")
        failing = failing_task_id if failing_task_id is not None else task_ids[-1]
        results: list[TaskResult] = []
        for task_id in task_ids:
            passed = generation > 0 or task_id != failing
            results.append(
                TaskResult(
                    task_id=task_id,
                    outcome=TaskOutcome.PASSED if passed else TaskOutcome.FAILED,
                    reward=1.0 if passed else 0.0,
                )
            )
        return tuple(results)


def task_result_from_verifier(task_id: str, receipt: VerifierReceipt) -> TaskResult:
    from syndicate.services.benchmark import RunOutcome

    if receipt.outcome is RunOutcome.PASS:
        return TaskResult(task_id=task_id, outcome=TaskOutcome.PASSED, reward=1.0)
    if receipt.outcome is RunOutcome.FAIL:
        return TaskResult(task_id=task_id, outcome=TaskOutcome.FAILED, reward=0.0)
    return TaskResult(task_id=task_id, outcome=TaskOutcome.INFRA_ERROR, reward=0.0)


class HarborExecutor:
    """Harbor/E2B path. Inject a runner, or fail closed without credentials."""

    kind = ExecutorKind.HARBOR

    def __init__(self, runner: HarborTask | None = None) -> None:
        self._runner = runner

    def run(self, task_ids: tuple[str, ...], generation: int) -> tuple[TaskResult, ...]:
        if self._runner is not None:
            return tuple(self._runner(task_id, generation) for task_id in task_ids)
        if not os.environ.get("E2B_API_KEY"):
            raise ValueError("E2B_API_KEY is required for harbor executor")
        if os.environ.get("SYNDICATE_HARBOR_STUB") == "1":
            return SimulatedExecutor().run(task_ids, generation)
        from syndicate.services.harbor_dispatch import run_bound_harbor_task

        return tuple(run_bound_harbor_task(task_id, generation) for task_id in task_ids)


def score_of(results: tuple[TaskResult, ...]) -> float:
    if not results:
        raise ValueError("results must not be empty")
    return sum(item.reward for item in results) / len(results)
