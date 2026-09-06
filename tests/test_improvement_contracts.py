from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from syndicate.evidence_contracts import SpanCitation
from syndicate.improvement_contracts import (
    CandidateCheck,
    CheckStatus,
    EditScope,
    FailureDiagnosis,
    HarnessChangeManifest,
    MetricEffect,
    MetricName,
    Prediction,
)
from syndicate.judge_contracts import (
    Finding,
    FindingCategory,
    ReportStatus,
    RunCoverage,
    TaskReport,
)

RUN = UUID(int=1)
CITATION = SpanCitation(run_id=RUN, trace_ref="a" * 32, span_ref="b" * 16)


def report(status: ReportStatus = ReportStatus.COMPLETE) -> TaskReport:
    return TaskReport(
        task_id="task-a-1",
        judge_spec_hash="c" * 64,
        run_ids=(RUN,),
        status=status,
        findings=(
            Finding(
                finding_id="finding-1",
                category=FindingCategory.MISSED_RECORDS,
                observation="The trace stopped after the first page.",
                hypothesis="Pagination may be missing.",
                evidence=(CITATION,),
            ),
        ),
        coverage=(RunCoverage(run_id=RUN, examined=(CITATION,)),),
    )


def diagnosis(status: ReportStatus = ReportStatus.COMPLETE) -> FailureDiagnosis:
    return FailureDiagnosis(
        diagnosis_id="diagnosis-1",
        campaign_id="campaign-1",
        parent_harness_hash="sha256:" + "d" * 64,
        reports=(report(status),),
        evidence_refs=(CITATION,),
        observed_pattern="Several runs end after a first response page.",
        competing_hypotheses=(
            "The response wrapper omits continuation metadata.",
            "The provider has no additional pages.",
        ),
        root_cause_hypothesis="The response wrapper omits continuation metadata.",
        edit_scope=EditScope(
            allowed_paths=(
                "harnesses/seed/tool_descriptions/run_shell_command.tool.yaml",
            ),
            target_paths=(
                "harnesses/seed/tool_descriptions/run_shell_command.tool.yaml",
            ),
        ),
    )


def test_diagnosis_requires_complete_reports_and_exact_report_evidence() -> None:
    complete = diagnosis()
    assert complete.evidence_refs == (CITATION,)
    with pytest.raises(ValidationError, match="complete"):
        diagnosis(ReportStatus.INCOMPLETE)
    with pytest.raises(ValidationError, match="report finding"):
        FailureDiagnosis.model_validate(
            diagnosis()
            .model_copy(
                update={
                    "evidence_refs": (
                        SpanCitation(run_id=RUN, trace_ref="a" * 32, span_ref="c" * 16),
                    )
                }
            )
            .model_dump()
        )


def test_diagnosis_rejects_unlocalized_or_judge_edit_scope() -> None:
    with pytest.raises(ValidationError, match="target"):
        EditScope(allowed_paths=("harnesses/seed/systemprompt.md",), target_paths=())
    with pytest.raises(ValidationError, match="frozen"):
        EditScope(
            allowed_paths=("src/syndicate/judge_contracts.py",),
            target_paths=("src/syndicate/judge_contracts.py",),
        )


def test_manifest_requires_diagnosis_owned_evidence_and_passing_checks() -> None:
    source = diagnosis()
    manifest = HarnessChangeManifest(
        candidate_id="candidate-1",
        diagnosis=source,
        diff_hash="sha256:" + "e" * 64,
        intended_fix="Expose continuation metadata to the shared tool prompt.",
        expected_affected_tasks=("task-a-1",),
        at_risk_tasks=("task-a-2",),
        metric_effects=(
            MetricEffect(metric=MetricName.CORRECTNESS, prediction=Prediction.INCREASE),
            MetricEffect(metric=MetricName.COST, prediction=Prediction.UNCERTAIN),
        ),
        focused_checks=(
            CandidateCheck(
                command="uv run pytest tests/test_shell.py", status=CheckStatus.PASSED
            ),
        ),
        submitted_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    assert manifest.parent_harness_hash == source.parent_harness_hash
    with pytest.raises(ValidationError, match="passing"):
        HarnessChangeManifest.model_validate(
            manifest.model_copy(
                update={
                    "focused_checks": (
                        CandidateCheck(
                            command="uv run pytest", status=CheckStatus.FAILED
                        ),
                    )
                }
            ).model_dump()
        )
