"""Evidence-bound diagnosis and pre-evaluation candidate manifests."""

from enum import StrEnum
from typing import Annotated, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from syndicate.evidence_contracts import SpanCitation
from syndicate.judge_contracts import ReportStatus, TaskReport

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Path = Annotated[
    str,
    Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9._/-]+$",
    ),
]


class ImprovementObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class MetricName(StrEnum):
    CORRECTNESS = "correctness"
    RELIABILITY = "reliability"
    COST = "cost"
    LATENCY = "latency"


class Prediction(StrEnum):
    INCREASE = "increase"
    DECREASE = "decrease"
    UNCERTAIN = "uncertain"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class EditScope(ImprovementObject):
    """One localized shared-harness edit surface; judges remain frozen."""

    allowed_paths: tuple[Path, ...] = Field(min_length=1)
    target_paths: tuple[Path, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def localized_and_not_judges(self) -> Self:
        if any(
            path.startswith("/") or ".." in path.split("/")
            for path in self.target_paths + self.allowed_paths
        ):
            raise ValueError(
                "Edit paths must be relative and cannot escape the harness"
            )
        if not set(self.target_paths).issubset(self.allowed_paths):
            raise ValueError("Target paths must be within the allowed edit scope")
        if any("judge" in path.lower() for path in self.target_paths):
            raise ValueError("Judge paths are frozen controls")
        return self


def _complete_report_refs(reports: tuple[TaskReport, ...]) -> set[SpanCitation]:
    if len({report.task_id for report in reports}) != len(reports):
        raise ValueError("Diagnosis reports must have unique task IDs")
    if any(report.status is not ReportStatus.COMPLETE for report in reports):
        raise ValueError("Failure diagnosis requires complete judge reports")
    return {
        reference
        for report in reports
        for finding in report.findings
        for reference in finding.evidence
        if isinstance(reference, SpanCitation)
    }


class FailureDiagnosis(ImprovementObject):
    diagnosis_id: Text
    campaign_id: Text
    parent_harness_hash: Digest
    reports: tuple[TaskReport, ...] = Field(min_length=1)
    evidence_refs: tuple[SpanCitation, ...] = Field(min_length=1)
    observed_pattern: Text
    competing_hypotheses: tuple[Text, ...] = Field(min_length=2)
    root_cause_hypothesis: Text
    edit_scope: EditScope

    @model_validator(mode="after")
    def evidence_is_complete_and_exact(self) -> Self:
        if self.root_cause_hypothesis not in self.competing_hypotheses:
            raise ValueError("Root cause must be one of the competing hypotheses")
        if not set(self.evidence_refs).issubset(_complete_report_refs(self.reports)):
            raise ValueError("Diagnosis evidence must exactly cite a report finding")
        return self


class MetricEffect(ImprovementObject):
    metric: MetricName
    prediction: Prediction


class CandidateCheck(ImprovementObject):
    command: Text
    status: CheckStatus


class HarnessChangeManifest(ImprovementObject):
    candidate_id: Text
    diagnosis: FailureDiagnosis
    diff_hash: Digest
    intended_fix: Text
    expected_affected_tasks: tuple[Text, ...] = Field(min_length=1)
    at_risk_tasks: tuple[Text, ...]
    metric_effects: tuple[MetricEffect, ...] = Field(min_length=1)
    focused_checks: tuple[CandidateCheck, ...] = Field(min_length=1)
    submitted_at: AwareDatetime

    @property
    def parent_harness_hash(self) -> str:
        return self.diagnosis.parent_harness_hash

    @property
    def target_paths(self) -> tuple[str, ...]:
        return self.diagnosis.edit_scope.target_paths

    @property
    def evidence_refs(self) -> tuple[SpanCitation, ...]:
        return self.diagnosis.evidence_refs

    @model_validator(mode="after")
    def complete_pre_evaluation_submission(self) -> Self:
        if len({effect.metric for effect in self.metric_effects}) != len(
            self.metric_effects
        ):
            raise ValueError("Metric effects must be unique")
        if any(check.status is not CheckStatus.PASSED for check in self.focused_checks):
            raise ValueError("Manifest requires passing focused checks")
        return self
