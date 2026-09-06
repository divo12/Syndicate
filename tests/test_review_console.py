"""Public rendering seam for the read-only P27 review console."""

from datetime import UTC, datetime
from uuid import UUID

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
from syndicate.review_console import ReceiptSource, ReviewCampaign, render_campaign

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
DIGEST = "sha256:" + "a" * 64
TRACE_REF = "b" * 32
SPAN_REF = "c" * 16


def report() -> TaskReport:
    citation = SpanCitation(run_id=RUN_ID, trace_ref=TRACE_REF, span_ref=SPAN_REF)
    return TaskReport(
        task_id="task-a-1",
        judge_spec_hash=DIGEST,
        run_ids=(RUN_ID,),
        status=ReportStatus.COMPLETE,
        findings=(
            Finding(
                finding_id="finding-1",
                category=FindingCategory.MISSED_RECORDS,
                observation="Second page was not read.",
                hypothesis="Pagination is missing.",
                evidence=(citation,),
            ),
        ),
        coverage=(RunCoverage(run_id=RUN_ID, examined=(citation,)),),
    )


def manifest(task_report: TaskReport) -> HarnessChangeManifest:
    citation = task_report.findings[0].evidence[0]
    assert isinstance(citation, SpanCitation)
    diagnosis = FailureDiagnosis(
        diagnosis_id="diagnosis-1",
        campaign_id="campaign-1",
        parent_harness_hash=DIGEST,
        reports=(task_report,),
        evidence_refs=(citation,),
        observed_pattern="Only the first page is used.",
        competing_hypotheses=("Pagination is missing.", "Search is incomplete."),
        root_cause_hypothesis="Pagination is missing.",
        edit_scope=EditScope(
            allowed_paths=("harnesses/seed/systemprompt.md",),
            target_paths=("harnesses/seed/systemprompt.md",),
        ),
    )
    return HarnessChangeManifest(
        candidate_id="candidate-1",
        diagnosis=diagnosis,
        diff_hash=DIGEST,
        intended_fix="Check pagination before completion.",
        expected_affected_tasks=("task-a-1",),
        at_risk_tasks=(),
        metric_effects=(
            MetricEffect(metric=MetricName.CORRECTNESS, prediction=Prediction.INCREASE),
        ),
        focused_checks=(
            CandidateCheck(
                command="pytest tests/test_shell.py", status=CheckStatus.PASSED
            ),
        ),
        submitted_at=datetime(2026, 9, 6, tzinfo=UTC),
    )


def test_render_campaign_shows_typed_receipts_and_remote_refs() -> None:
    task_report = report()
    page = render_campaign(
        ReviewCampaign(
            campaign_id="campaign-1",
            source=ReceiptSource.SYNTHETIC,
            reports=(task_report,),
            candidates=(manifest(task_report),),
        )
    )

    assert "Synthetic preparation data" in page
    assert "task-a-1" in page
    assert "finding-1" in page
    assert f"{TRACE_REF}/{SPAN_REF}" in page
    assert "Second page was not read." in page
    assert "Check pagination before completion." in page


def test_render_campaign_escapes_receipt_text() -> None:
    task_report = report().model_copy(
        update={
            "findings": (
                report()
                .findings[0]
                .model_copy(update={"observation": "<script>no</script>"}),
            )
        }
    )

    page = render_campaign(
        ReviewCampaign(
            campaign_id="campaign-1",
            source=ReceiptSource.RECORDED,
            reports=(task_report,),
        )
    )

    assert "&lt;script&gt;no&lt;/script&gt;" in page
    assert "<script>no</script>" not in page
