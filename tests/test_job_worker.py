from pathlib import Path

import pytest

from syndicate.adapters.trigger_jobs import NullTriggerLoop, trigger_from_env
from syndicate.models.jobs import Job, JobStatus, JobSubmission, StopReason
from syndicate.repositories.jobs import SqliteJobStore
from syndicate.services.executors import SimulatedExecutor, score_of
from syndicate.services.job_worker import JobWorker


def _store(tmp_path: Path) -> SqliteJobStore:
    return SqliteJobStore(tmp_path / "jobs.sqlite")


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


def test_worker_dispatches_trigger_and_persists_live_iterations(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
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


def test_worker_runs_without_trigger_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TRIGGER_SECRET_KEY", raising=False)
    monkeypatch.delenv("TRIGGER_API_URL", raising=False)
    store = _store(tmp_path)
    store.create(JobSubmission(task_ids=("regex-log",)))
    finished = JobWorker(store, trigger_from_env(None, None)).process_one()
    assert finished is not None
    assert finished.trigger_run_id is None
    assert finished.status is JobStatus.COMPLETED


def test_default_improve_mines_failures_into_next_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACT_ROOT", str(tmp_path))
    trial = tmp_path / "runs" / "op" / "att" / "harbor" / "trial"
    verifier = trial / "verifier"
    verifier.mkdir(parents=True)
    (trial / "result.json").write_text(
        '{"task_name": "abhishek203/task-a-1"}', encoding="utf-8"
    )
    (trial / "trial.log").write_text("Maximum iteration limit reached\n")
    (verifier / "ctrf.json").write_text(
        '{"results": {"tests": ['
        '{"name": "test_gw_account_suspended", "status": "failed"}'
        "]}}",
        encoding="utf-8",
    )
    store = _store(tmp_path)
    store.create(JobSubmission(task_ids=("regex-log",), max_iterations=2, patience=2))
    root = tmp_path / "lineage"
    finished = JobWorker(store, NullTriggerLoop(), lineage_root=root).process_one()
    assert finished is not None
    failures = (root / f"{finished.id}.failures.json").read_text(encoding="utf-8")
    assert "test_gw_account_suspended" in failures
    assert "task-a-1" in failures
    lessons = (tmp_path / "harnesses" / "gen-1.lessons.md").read_text(encoding="utf-8")
    assert "test_gw_account_suspended" in lessons
    assert "tests/test.sh" in lessons


def test_default_improve_writes_receipt(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(JobSubmission(task_ids=("regex-log",), max_iterations=2, patience=2))
    root = tmp_path / "lineage"
    finished = JobWorker(store, NullTriggerLoop(), lineage_root=root).process_one()
    assert finished is not None
    assert finished.status is JobStatus.COMPLETED
    receipt = root / f"{finished.id}.improve"
    assert receipt.read_text(encoding="utf-8") == "0->1\n"


def test_trigger_dispatch_failure_marks_job_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(JobSubmission(task_ids=("regex-log",)))

    class Boom:
        def start_loop(self, job: Job) -> str:
            del job
            raise RuntimeError("trigger down")

    finished = JobWorker(store, Boom()).process_one()
    assert finished is not None
    assert finished.status is JobStatus.FAILED
    assert finished.error is not None
    assert "trigger down" in finished.error


def test_loop_exception_marks_job_failed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.create(JobSubmission(task_ids=("regex-log",), max_iterations=2, patience=2))

    def boom(generation: int) -> int:
        del generation
        raise RuntimeError("improve exploded")

    finished = JobWorker(store, NullTriggerLoop(), improve=boom).process_one()
    assert finished is not None
    assert finished.status is JobStatus.FAILED
    assert finished.error is not None
