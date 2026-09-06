from syndicate.adapters.trigger_jobs import NullTriggerLoop
from syndicate.models.jobs import Job, JobStatus, JobSubmission, StopReason
from syndicate.repositories.jobs import MemoryJobStore
from syndicate.services.executors import SimulatedExecutor, score_of
from syndicate.services.job_worker import JobWorker


class RecordingTrigger:
    def __init__(self) -> None:
        self.jobs: list[str] = []

    def start_loop(self, job: Job) -> str:
        self.jobs.append(str(job.id))
        return "run_trigger_1"


def test_simulated_executor_fails_only_the_last_baseline_task() -> None:
    results = SimulatedExecutor().run(("a", "b", "c"), 0)
    assert [item.outcome.value for item in results] == ["passed", "passed", "failed"]
    assert score_of(SimulatedExecutor().run(("a", "b", "c"), 1)) == 1


def test_worker_dispatches_trigger_and_persists_live_iterations() -> None:
    store = MemoryJobStore()
    job = store.create(
        JobSubmission(
            task_ids=("regex-log", "extract-elf"), max_iterations=3, patience=2
        )
    )
    trigger = RecordingTrigger()
    finished = JobWorker(store, trigger).process_one()
    assert finished is not None
    assert trigger.jobs == [str(job.id)]
    assert finished.trigger_run_id == "run_trigger_1"
    assert finished.status is JobStatus.COMPLETED
    assert finished.stop_reason is StopReason.ALL_TASKS_PASSED
    assert finished.best_score == 1
    iterations = store.iterations(job.id)
    assert len(iterations) == 2
    assert iterations[0].score == 0.5
    assert iterations[1].score == 1


def test_worker_runs_without_trigger_credentials() -> None:
    store = MemoryJobStore()
    store.create(JobSubmission(task_ids=("regex-log",)))
    finished = JobWorker(store, NullTriggerLoop()).process_one()
    assert finished is not None
    assert finished.trigger_run_id is None
    assert finished.status is JobStatus.COMPLETED
