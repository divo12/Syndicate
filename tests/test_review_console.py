"""Public rendering seam for the read-only P27 review console."""

from datetime import UTC, datetime
from uuid import UUID

from syndicate.comparison_contracts import PairSchedule
from syndicate.evidence_contracts import RecordCitation, SpanCitation
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
from syndicate.review_navigation import render_finding_path
from syndicate.review_report import HeldOutEvaluation, HeldOutStatus, ReviewReport
from syndicate.selection_contracts import (
    ArmMetrics,
    ComparisonAssessment,
    ComparisonDecision,
)

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


def report_with_record() -> TaskReport:
    record = RecordCitation(run_id=RUN_ID, record_ref="verifier:task-a-1")
    return report().model_copy(
        update={
            "findings": report().findings
            + (
                Finding(
                    finding_id="finding-2",
                    category=FindingCategory.UNSUPPORTED_CLAIM,
                    observation="Verifier evidence needs review.",
                    evidence=(record,),
                ),
            )
        }
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


def test_render_campaign_shows_record_citations_without_trace_payloads() -> None:
    page = render_campaign(
        ReviewCampaign(
            campaign_id="campaign-1",
            source=ReceiptSource.SYNTHETIC,
            reports=(report_with_record(),),
        )
    )

    assert "verifier:task-a-1" in page


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


def test_render_finding_path_links_receipts_to_the_paired_comparison() -> None:
    task_report = report()
    candidate = manifest(task_report).model_copy(
        update={"diff_hash": "sha256:" + "0" * 64}
    )
    schedule = PairSchedule(
        campaign_id="campaign-1",
        incumbent_harness_hash=DIGEST,
        candidate_harness_hash="sha256:" + "f" * 64,
        candidate_diff_hash=candidate.diff_hash,
        pairs=(),
    )
    assessment = ComparisonAssessment(
        decision=ComparisonDecision.INCONCLUSIVE,
        incumbent=ArmMetrics(
            success_rate=0.0,
            reliability=0.0,
            cost_per_success_microusd=None,
            median_elapsed_ms=0.0,
        ),
        candidate=ArmMetrics(
            success_rate=0.0,
            reliability=0.0,
            cost_per_success_microusd=None,
            median_elapsed_ms=0.0,
        ),
        reason_codes=("evidence-incomplete",),
    )

    page = render_finding_path(
        task_report,
        "finding-1",
        candidate,
        schedule,
        assessment,
        ReceiptSource.SYNTHETIC,
    )

    assert f"{TRACE_REF}/{SPAN_REF}" in page
    assert "diagnosis-1" in page
    assert candidate.diff_hash in page
    assert "inconclusive" in page
    assert "Synthetic preparation data" in page


def test_render_finding_path_selects_a_record_citation_from_many_findings() -> None:
    task_report = report_with_record()
    candidate = manifest(task_report).model_copy(
        update={"diff_hash": "sha256:" + "0" * 64}
    )
    schedule = PairSchedule(
        campaign_id="campaign-1",
        incumbent_harness_hash=DIGEST,
        candidate_harness_hash="sha256:" + "f" * 64,
        candidate_diff_hash=candidate.diff_hash,
        pairs=(),
    )
    assessment = ComparisonAssessment(
        decision=ComparisonDecision.INCONCLUSIVE,
        incumbent=ArmMetrics(
            success_rate=0.0,
            reliability=0.0,
            cost_per_success_microusd=None,
            median_elapsed_ms=0.0,
        ),
        candidate=ArmMetrics(
            success_rate=0.0,
            reliability=0.0,
            cost_per_success_microusd=None,
            median_elapsed_ms=0.0,
        ),
        reason_codes=("evidence-incomplete",),
    )

    page = render_finding_path(
        task_report,
        "finding-2",
        candidate,
        schedule,
        assessment,
        ReceiptSource.SYNTHETIC,
    )

    assert "verifier:task-a-1" in page


def test_report_serializes_limits_without_claiming_held_out_results() -> None:
    campaign = ReviewCampaign(
        campaign_id="campaign-1",
        source=ReceiptSource.SYNTHETIC,
        reports=(report(),),
    )
    assessment = ComparisonAssessment(
        decision=ComparisonDecision.INCONCLUSIVE,
        incumbent=ArmMetrics(
            success_rate=0.0,
            reliability=0.0,
            cost_per_success_microusd=None,
            median_elapsed_ms=0.0,
        ),
        candidate=ArmMetrics(
            success_rate=0.0,
            reliability=0.0,
            cost_per_success_microusd=None,
            median_elapsed_ms=0.0,
        ),
        reason_codes=("evidence-incomplete",),
    )
    report_view = ReviewReport(
        campaign=campaign,
        assessment=assessment,
        held_out=HeldOutEvaluation(
            status=HeldOutStatus.NOT_RUN,
            task_ids=("task-held-out-1",),
            limitation="Held-out evaluation has not run.",
        ),
        limitations=("Synthetic preparation data is not campaign evidence.",),
    )

    assert "not_run" in report_view.to_json()
    assert "Held-out evaluation has not run." in report_view.to_markdown()
    assert "Synthetic preparation data" in report_view.to_markdown()
