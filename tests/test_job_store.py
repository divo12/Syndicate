from syndicate.models.jobs import JobStatus, JobSubmission
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
