import pytest
from test_task_judge import report

from syndicate.models.collection import CollectionStatus, ExpectedTaskReport
from syndicate.models.judging import FindingCategory, ReportStatus, TaskReport
from syndicate.services.collection import collect_reports


def expected(task_id: str, spec_hash: str = "spec:one") -> ExpectedTaskReport:
    return ExpectedTaskReport(task_id=task_id, judge_spec_hash=spec_hash)


def task_report(task_id: str, spec_hash: str = "spec:one") -> TaskReport:
    draft = report()
    return TaskReport(
        task_id=task_id,
        judge_spec_hash=spec_hash,
        run_ids=draft.run_ids,
        status=draft.status,
        findings=draft.findings,
        coverage=draft.coverage,
        usage_ref="usage:1",
    )


def test_missing_report_is_indexed_and_blocks_diagnosis() -> None:
    collection = collect_reports(
        (expected("task-a"), expected("task-b")), (task_report("task-a"),)
    )
    assert collection.entries[1].status is CollectionStatus.MISSING
    assert collection.missing_task_ids == ("task-b",)
    assert not collection.ready_for_diagnosis
    assert not collection.admissible_for_promotion


def test_explicit_incomplete_report_is_terminal_but_not_promotable() -> None:
    incomplete = task_report("task-a").model_copy(
        update={"status": ReportStatus.INCOMPLETE}
    )
    collection = collect_reports((expected("task-a"),), (incomplete,))
    assert collection.incomplete_task_ids == ("task-a",)
    assert collection.ready_for_diagnosis
    assert not collection.admissible_for_promotion


def test_overview_retains_usage_and_repeated_categories() -> None:
    first = task_report("task-a")
    second = task_report("task-b").model_copy(update={"findings": first.findings})
    collection = collect_reports(
        (expected("task-a"), expected("task-b")), (first, second)
    )
    assert collection.entries[0].usage_ref == "usage:1"
    assert collection.repeated_categories == (FindingCategory.UNSUPPORTED_CLAIM,)
    assert collection.admissible_for_promotion


@pytest.mark.parametrize(
    "reports", [(task_report("task-z"),), (task_report("task-a", "spec:other"),)]
)
def test_unknown_or_wrong_spec_report_is_rejected(
    reports: tuple[TaskReport, ...],
) -> None:
    with pytest.raises(ValueError):
        collect_reports((expected("task-a"),), reports)


def test_duplicate_expected_or_received_task_is_rejected() -> None:
    with pytest.raises(ValueError, match="Expected"):
        collect_reports((expected("task-a"), expected("task-a")), ())
    with pytest.raises(ValueError, match="Reports"):
        collect_reports(
            (expected("task-a"),), (task_report("task-a"), task_report("task-a"))
        )
