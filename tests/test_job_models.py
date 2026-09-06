from syndicate.models.jobs import ExecutorKind, JobStatus, JobSubmission


def test_submission_requires_unique_nonempty_task_ids() -> None:
    JobSubmission(task_ids=("regex-log",))
    JobSubmission(task_ids=("regex-log", "extract-elf"), max_iterations=3, patience=2)
    try:
        JobSubmission(task_ids=())
    except ValueError:
        pass
    else:
        raise AssertionError("empty task_ids must fail")
    try:
        JobSubmission(task_ids=("regex-log", "regex-log"))
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate task_ids must fail")


def test_job_status_and_executor_are_closed_enums() -> None:
    assert JobStatus.QUEUED.value == "queued"
    assert {item.value for item in JobStatus} == {
        "queued",
        "running",
        "completed",
        "failed",
        "cancelled",
    }
    assert {item.value for item in ExecutorKind} == {"simulated", "harbor"}
