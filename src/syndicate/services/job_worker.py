"""Claim a queued job, dispatch Trigger.dev, then run the Python learning loop."""

import os
from pathlib import Path
from uuid import UUID

from syndicate.adapters.trigger_jobs import TriggerLoop
from syndicate.models.jobs import ExecutorKind, Job, JobStatus, StopReason
from syndicate.repositories.jobs import JobStore
from syndicate.services.ahe_improve import Analyze, Evolve, improve_generation
from syndicate.services.executors import HarborExecutor, SimulatedExecutor
from syndicate.services.failure_mine import mine_latest
from syndicate.services.learning_loop import Improve, LoopReceipt, run_outer_loop
from syndicate.services.lineage import HarnessLineage


class JobWorker:
    def __init__(
        self,
        store: JobStore,
        trigger: TriggerLoop,
        lineage_root: Path | None = None,
        improve: Improve | None = None,
        analyze: Analyze | None = None,
        evolve: Evolve | None = None,
    ) -> None:
        self._store = store
        self._trigger = trigger
        self._lineage_root = lineage_root
        self._improve = improve
        self._analyze = analyze
        self._evolve = evolve
        self._executor = SimulatedExecutor()

    def process_one(self) -> Job | None:
        job = self._store.claim()
        if job is None:
            return None
        try:
            self._dispatch(job)
            receipt = self._run(job)
        except Exception as error:
            return self._fail(job.id, error)
        return self._persist(job.id, receipt)

    def _dispatch(self, job: Job) -> None:
        run_id = self._trigger.start_loop(job)
        if run_id is not None:
            self._store.attach_trigger(job.id, run_id)

    def _run(self, job: Job) -> LoopReceipt:
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
        bump = self._improve or (lambda generation: self._bump(job, generation))
        return run_outer_loop(
            job,
            executor=runner,
            improve=bump,
            lineage=lineage,
            cancelled=lambda: self._cancelled(job.id),
        )

    def _bump(self, job: Job, generation: int) -> int:
        nxt = generation + 1
        job_id = job.id
        artifact_root = Path(os.environ.get("ARTIFACT_ROOT", ".syndicate"))
        seed = Path(os.environ.get("HARNESS_SEED", "harnesses/seed"))
        mine = mine_latest(artifact_root / "runs", generation)
        ahe = (
            job.executor is ExecutorKind.HARBOR
            or self._analyze is not None
            or self._evolve is not None
        )
        if mine.tasks and ahe:
            improve_generation(
                artifact_root,
                nxt,
                seed,
                analyze=self._analyze,
                evolve=self._evolve,
            )
            if self._lineage_root is not None:
                failures = self._lineage_root / f"{job_id}.failures.json"
                failures.parent.mkdir(parents=True, exist_ok=True)
                failures.write_text(mine.model_dump_json(indent=2), encoding="utf-8")
        if self._lineage_root is None:
            return nxt
        path = self._lineage_root / f"{job_id}.improve"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{generation}->{nxt}\n", encoding="utf-8")
        return nxt

    def _cancelled(self, job_id: UUID) -> bool:
        current = self._store.get(job_id)
        return current is not None and current.status is JobStatus.CANCELLED

    def _fail(self, job_id: UUID, error: Exception) -> Job | None:
        return self._store.finish(
            job_id, JobStatus.FAILED, StopReason.ERROR, 0.0, str(error)
        )

    def _persist(self, job_id: UUID, receipt: LoopReceipt) -> Job | None:
        for iteration in receipt.iterations:
            self._store.save_iteration(iteration)
        if self._cancelled(job_id):
            return self._store.get(job_id)
        status = (
            JobStatus.CANCELLED
            if receipt.stop_reason is StopReason.CANCELLED
            else JobStatus.COMPLETED
        )
        return self._store.finish(
            job_id, status, receipt.stop_reason, receipt.best_score
        )
