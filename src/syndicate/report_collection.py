"""Deterministic task-report collection without trajectory payload storage."""

from collections import Counter
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from syndicate.judge_contracts import FindingCategory, ReportStatus, TaskReport


class CollectionObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class CollectionStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    MISSING = "missing"


class ExpectedTaskReport(CollectionObject):
    task_id: str = Field(min_length=1)
    judge_spec_hash: str = Field(min_length=1)


class ReportIndexEntry(CollectionObject):
    task_id: str = Field(min_length=1)
    judge_spec_hash: str = Field(min_length=1)
    status: CollectionStatus
    usage_ref: str | None = None
    finding_categories: tuple[FindingCategory, ...] = ()


class ReportCollection(CollectionObject):
    entries: tuple[ReportIndexEntry, ...] = Field(min_length=1)
    repeated_categories: tuple[FindingCategory, ...] = ()

    @model_validator(mode="after")
    def unique_tasks(self) -> "ReportCollection":
        task_ids = tuple(entry.task_id for entry in self.entries)
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Collection entries must have unique task IDs")
        return self

    @property
    def missing_task_ids(self) -> tuple[str, ...]:
        return tuple(
            entry.task_id
            for entry in self.entries
            if entry.status is CollectionStatus.MISSING
        )

    @property
    def incomplete_task_ids(self) -> tuple[str, ...]:
        return tuple(
            entry.task_id
            for entry in self.entries
            if entry.status is CollectionStatus.INCOMPLETE
        )

    @property
    def ready_for_diagnosis(self) -> bool:
        return not self.missing_task_ids

    @property
    def admissible_for_promotion(self) -> bool:
        return all(entry.status is CollectionStatus.COMPLETE for entry in self.entries)


def collect_reports(
    expected: tuple[ExpectedTaskReport, ...], reports: tuple[TaskReport, ...]
) -> ReportCollection:
    """Index every expected task, preserving missing and incomplete outcomes."""
    expected_by_task = _unique_expected(expected)
    reports_by_task = _unique_reports(reports)
    if reports_by_task.keys() - expected_by_task.keys():
        raise ValueError("Report is not in the expected task manifest")
    entries = tuple(
        _entry(item, reports_by_task.get(item.task_id)) for item in expected
    )
    return ReportCollection(entries=entries, repeated_categories=_repeated(entries))


def _unique_expected(
    expected: tuple[ExpectedTaskReport, ...],
) -> dict[str, ExpectedTaskReport]:
    indexed = {item.task_id: item for item in expected}
    if len(indexed) != len(expected):
        raise ValueError("Expected reports must have unique task IDs")
    return indexed


def _unique_reports(reports: tuple[TaskReport, ...]) -> dict[str, TaskReport]:
    indexed = {report.task_id: report for report in reports}
    if len(indexed) != len(reports):
        raise ValueError("Reports must have unique task IDs")
    return indexed


def _repeated(entries: tuple[ReportIndexEntry, ...]) -> tuple[FindingCategory, ...]:
    counts = Counter(
        category for entry in entries for category in entry.finding_categories
    )
    return tuple(category for category, count in counts.items() if count > 1)


def _entry(expected: ExpectedTaskReport, report: TaskReport | None) -> ReportIndexEntry:
    if report is None:
        return ReportIndexEntry(
            task_id=expected.task_id,
            judge_spec_hash=expected.judge_spec_hash,
            status=CollectionStatus.MISSING,
        )
    if report.judge_spec_hash != expected.judge_spec_hash:
        raise ValueError("Report judge specification does not match expected manifest")
    return ReportIndexEntry(
        task_id=report.task_id,
        judge_spec_hash=report.judge_spec_hash,
        status=(
            CollectionStatus.COMPLETE
            if report.status is ReportStatus.COMPLETE
            else CollectionStatus.INCOMPLETE
        ),
        usage_ref=report.usage_ref,
        finding_categories=tuple(finding.category for finding in report.findings),
    )
