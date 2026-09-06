"""Read-only receipt navigation from a finding to its paired assessment."""

from html import escape

from syndicate.models.comparison import PairSchedule
from syndicate.models.evidence import SpanCitation
from syndicate.models.improvement import HarnessChangeManifest
from syndicate.models.judging import Finding, TaskReport
from syndicate.models.review import ReceiptSource
from syndicate.models.selection import ComparisonAssessment


def _single_finding(report: TaskReport) -> Finding:
    if len(report.findings) != 1:
        raise ValueError("Finding navigation requires one selected finding")
    return report.findings[0]


def _remote_refs(finding: Finding) -> str:
    references = tuple(
        f"{citation.trace_ref}/{citation.span_ref}"
        for citation in finding.evidence
        if isinstance(citation, SpanCitation)
    )
    if not references:
        raise ValueError("Finding navigation requires a remote span reference")
    return ", ".join(escape(reference) for reference in references)


def _validate_path(
    report: TaskReport, candidate: HarnessChangeManifest, schedule: PairSchedule
) -> Finding:
    finding = _single_finding(report)
    if report not in candidate.diagnosis.reports:
        raise ValueError("Candidate diagnosis does not contain the selected report")
    if schedule.campaign_id != candidate.diagnosis.campaign_id:
        raise ValueError("Paired schedule belongs to another campaign")
    if schedule.candidate_diff_hash != candidate.diff_hash:
        raise ValueError("Paired schedule does not match the candidate diff")
    _remote_refs(finding)
    return finding


def render_finding_path(
    report: TaskReport,
    candidate: HarnessChangeManifest,
    schedule: PairSchedule,
    assessment: ComparisonAssessment,
    source: ReceiptSource,
) -> str:
    """Render linked IDs only; comparison outcomes are not computed here."""
    finding = _validate_path(report, candidate, schedule)
    source_label = (
        "Synthetic preparation data"
        if source is ReceiptSource.SYNTHETIC
        else "Recorded receipts"
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Syndicate finding path</title></head><body><main><p>"
        + source_label
        + "</p><h1>"
        + escape(finding.finding_id)
        + "</h1><section><h2>Evidence</h2><code>"
        + _remote_refs(finding)
        + "</code></section><section><h2>Diagnosis</h2><p>"
        + escape(candidate.diagnosis.diagnosis_id)
        + "</p></section><section><h2>Candidate diff</h2><code>"
        + escape(candidate.diff_hash)
        + "</code></section><section><h2>Paired comparison</h2><p>"
        + escape(assessment.decision.value)
        + "</p></section></main></body></html>"
    )
