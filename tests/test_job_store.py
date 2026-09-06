from syndicate.models.jobs import JobStatus, JobSubmission, StopReason
from syndicate.repositories.jobs import MemoryJobStore


def test_create_lists_and_gets_a_queued_job() -> None:
    store = MemoryJobStore()
    job = store.create(JobSubmission(task_ids=("regex-log", "extract-elf")))
    assert job.status is JobStatus.QUEUED
    assert job.trigger_run_id is None
    assert store.get(job.id) == job
    assert store.list_jobs() == (job,)
    assert store.list_jobs(JobStatus.QUEUED) == (job,)
    assert store.list_jobs(JobStatus.RUNNING) == ()


def test_cancel_is_allowed_from_queued_only() -> None:
    store = MemoryJobStore()
    queued = store.create(JobSubmission(task_ids=("regex-log",)))
    cancelled = store.cancel(queued.id)
    assert cancelled is not None
    assert cancelled.status is JobStatus.CANCELLED
    assert store.cancel(queued.id) is None


def test_finish_does_not_overwrite_a_cancelled_job() -> None:
    store = MemoryJobStore()
    job = store.create(JobSubmission(task_ids=("regex-log",)))
    claimed = store.claim()
    assert claimed is not None
    cancelled = store.cancel(job.id)
    assert cancelled is not None
    finished = store.finish(
        job.id, JobStatus.COMPLETED, StopReason.ALL_TASKS_PASSED, 1.0
    )
    assert finished is not None
    assert finished.status is JobStatus.CANCELLED
