"""Safe static HTML for review campaigns; never fetches trace payloads."""

from html import escape

from syndicate.models.evidence import RecordCitation, SpanCitation
from syndicate.models.improvement import HarnessChangeManifest
from syndicate.models.judging import TaskReport
from syndicate.models.review import ReceiptSource, ReviewCampaign


def _citation(citation: SpanCitation | RecordCitation) -> str:
    reference = (
        f"{citation.trace_ref}/{citation.span_ref}"
        if isinstance(citation, SpanCitation)
        else citation.record_ref
    )
    return escape(reference)


def _finding_rows(report: TaskReport) -> str:
    rows: list[str] = []
    for finding in report.findings:
        references = ", ".join(
            _citation(citation)
            for citation in finding.evidence
            if isinstance(citation, SpanCitation | RecordCitation)
        )
        rows.append(
            "<li><strong>"
            + escape(finding.finding_id)
            + "</strong>: "
            + escape(finding.observation)
            + " <code>"
            + references
            + "</code></li>"
        )
    return "".join(rows) or "<li>No findings.</li>"


def _task_section(report: TaskReport) -> str:
    return (
        "<section><h2>"
        + escape(report.task_id)
        + "</h2><p>Judge status: "
        + escape(report.status.value)
        + "</p><ul>"
        + _finding_rows(report)
        + "</ul></section>"
    )


def _candidate_section(candidate: HarnessChangeManifest) -> str:
    return (
        "<li><strong>"
        + escape(candidate.candidate_id)
        + "</strong>: "
        + escape(candidate.intended_fix)
        + " <code>"
        + escape(candidate.diff_hash)
        + "</code></li>"
    )


def render_campaign(campaign: ReviewCampaign) -> str:
    """Render safe static HTML without fetching or retaining trace payloads."""
    source = (
        "Synthetic preparation data"
        if campaign.source is ReceiptSource.SYNTHETIC
        else "Recorded receipts"
    )
    candidates = "".join(_candidate_section(item) for item in campaign.candidates)
    candidate_view = "<li>No candidate receipts.</li>" if not candidates else candidates
    tasks = "".join(_task_section(report) for report in campaign.reports)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Syndicate review</title></head><body><main><h1>Campaign "
        + escape(campaign.campaign_id)
        + "</h1><p>"
        + source
        + "</p><h2>Candidates</h2><ul>"
        + candidate_view
        + "</ul>"
        + tasks
        + "</main></body></html>"
    )
