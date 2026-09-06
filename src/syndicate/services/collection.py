"""Index expected task reports, preserving missing and incomplete outcomes."""

from syndicate.models.collection import (
    ExpectedTaskReport,
    ReportCollection,
    index_entry,
    repeated_categories,
)
from syndicate.models.judging import TaskReport


def collect_reports(
    expected: tuple[ExpectedTaskReport, ...], reports: tuple[TaskReport, ...]
) -> ReportCollection:
    """Index every expected task, preserving missing and incomplete outcomes."""
    _unique_expected(expected)
    reports_by_task = _unique_reports(reports)
    if reports_by_task.keys() - {item.task_id for item in expected}:
        raise ValueError("Report is not in the expected task manifest")
    entries = tuple(
        index_entry(item, reports_by_task.get(item.task_id)) for item in expected
    )
    return ReportCollection(
        entries=entries, repeated_categories=repeated_categories(entries)
    )


def _unique_expected(
    expected: tuple[ExpectedTaskReport, ...],
) -> None:
    if len({item.task_id for item in expected}) != len(expected):
        raise ValueError("Expected reports must have unique task IDs")


def _unique_reports(reports: tuple[TaskReport, ...]) -> dict[str, TaskReport]:
    indexed = {report.task_id: report for report in reports}
    if len(indexed) != len(reports):
        raise ValueError("Reports must have unique task IDs")
    return indexed
