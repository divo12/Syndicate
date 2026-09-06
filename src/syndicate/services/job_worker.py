"""Claim a queued job, dispatch Trigger.dev, then run the Python learning loop."""

from pathlib import Path

from syndicate.adapters.trigger_jobs import TriggerLoop
from syndicate.models.jobs import ExecutorKind, Job, JobStatus, StopReason
from syndicate.repositories.jobs import JobStore
from syndicate.services.executors import HarborExecutor, SimulatedExecutor
from syndicate.services.learning_loop import Improve, run_outer_loop
from syndicate.services.lineage import HarnessLineage


class JobWorker:
    def __init__(
        self,
        store: JobStore,
        trigger: TriggerLoop,
        lineage_root: Path | None = None,
        improve: Improve | None = None,
    ) -> None:
        self._store = store
        self._trigger = trigger
        self._lineage_root = lineage_root
        self._improve = improve
        self._executor = SimulatedExecutor()

    def process_one(self) -> Job | None:
        job = self._store.claim()
        if job is None:
            return None
        run_id = self._trigger.start_loop(job)
        if run_id is not None:
            attached = self._store.attach_trigger(job.id, run_id)
            job = attached or job
        lineage = None
        if self._lineage_root is not None:
            digest = f"sha256:{0:064x}"
            lineage = HarnessLineage(
                self._lineage_root / f"{job.id}.sqlite", digest, digest
            )
        runner = (
            HarborExecutor().run
            if job.executor is ExecutorKind.HARBOR
            else self._executor.run
        )
        try:
            receipt = run_outer_loop(
                job, executor=runner, improve=self._improve, lineage=lineage
            )
        except ValueError as error:
            return self._store.finish(
                job.id, JobStatus.FAILED, StopReason.ERROR, 0.0, str(error)
            )
        for iteration in receipt.iterations:
            self._store.save_iteration(iteration)
        status = (
            JobStatus.CANCELLED
            if receipt.stop_reason is StopReason.CANCELLED
            else JobStatus.COMPLETED
        )
        return self._store.finish(
            job.id, status, receipt.stop_reason, receipt.best_score
        )
