from syndicate.models.jobs import (
    ExecutorKind,
    JobStatus,
    JobSubmission,
    TaskOutcome,
    TaskResult,
)


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


def test_task_result_rejects_inconsistent_reward() -> None:
    TaskResult(task_id="regex-log", outcome=TaskOutcome.PASSED, reward=1.0)
    try:
        TaskResult(task_id="regex-log", outcome=TaskOutcome.PASSED, reward=0.0)
    except ValueError:
        return
    raise AssertionError("passed results must score 1.0")
