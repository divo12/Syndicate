from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from syndicate.models.candidate import CandidateValidationError, CandidateWorkspace
from syndicate.models.evidence import SpanCitation
from syndicate.models.improvement import (
    EditScope,
    FailureDiagnosis,
    MetricEffect,
    MetricName,
    Prediction,
    ProposalDraft,
    ProposalEdit,
    ProposalRequest,
)
from syndicate.models.judging import (
    Finding,
    FindingCategory,
    ReportStatus,
    RunCoverage,
    TaskReport,
)
from syndicate.services.candidate import create_candidate_workspace
from syndicate.services.improvement import apply_proposal

RUN = UUID(int=1)
CITATION = SpanCitation(run_id=RUN, trace_ref="a" * 32, span_ref="b" * 16)
PATH = "harnesses/seed/systemprompt.md"


def diagnosis(parent_hash: str) -> FailureDiagnosis:
    report = TaskReport(
        task_id="task-a-1",
        judge_spec_hash="c" * 64,
        run_ids=(RUN,),
        status=ReportStatus.COMPLETE,
        findings=(
            Finding(
                finding_id="finding-1",
                category=FindingCategory.MISSED_RECORDS,
                observation="The agent stopped after one response page.",
                hypothesis="Pagination guidance may be missing.",
                evidence=(CITATION,),
            ),
        ),
        coverage=(RunCoverage(run_id=RUN, examined=(CITATION,)),),
    )
    return FailureDiagnosis(
        diagnosis_id="diagnosis-1",
        campaign_id="campaign-1",
        parent_harness_hash="sha256:" + parent_hash,
        reports=(report,),
        evidence_refs=(CITATION,),
        observed_pattern="Runs stop after the first response page.",
        competing_hypotheses=(
            "Pagination guidance is missing.",
            "The provider has no second page.",
        ),
        root_cause_hypothesis="Pagination guidance is missing.",
        edit_scope=EditScope(allowed_paths=(PATH,), target_paths=(PATH,)),
    )


def workspace(tmp_path: Path) -> CandidateWorkspace:
    incumbent = tmp_path / "incumbent"
    target = incumbent / PATH
    target.parent.mkdir(parents=True)
    target.write_text("Check the available operations.\n", encoding="utf-8")
    return create_candidate_workspace(incumbent, (PATH,), tmp_path / "workspaces")


def request(parent_hash: str) -> ProposalRequest:
    return ProposalRequest(
        candidate_id="candidate-1",
        diagnosis=diagnosis(parent_hash),
        usage_reservation_ref="reservation-1",
        focused_checks=("uv run pytest tests/test_shell.py",),
        submitted_at=datetime(2026, 9, 6, tzinfo=UTC),
    )


def draft(path: str = PATH) -> ProposalDraft:
    return ProposalDraft(
        edits=(
            ProposalEdit(
                path=path, content="Check every response page before completion.\n"
            ),
        ),
        intended_fix="Add generic pagination guidance.",
        expected_affected_tasks=("task-a-1",),
        at_risk_tasks=("task-a-2",),
        metric_effects=(
            MetricEffect(metric=MetricName.CORRECTNESS, prediction=Prediction.INCREASE),
        ),
    )


def test_injected_transport_applies_only_diagnosis_scoped_edits(tmp_path: Path) -> None:
    candidate = workspace(tmp_path)
    calls: list[ProposalRequest] = []

    def transport(value: ProposalRequest) -> str:
        calls.append(value)
        return draft().model_dump_json()

    receipt = apply_proposal(
        request(candidate.candidate_parent_hash),
        candidate,
        transport,
        lambda _, __: True,
    )

    assert calls[0].model == "gpt-5.4-mini"
    assert receipt.manifest.diff_hash == "sha256:" + receipt.seal.diff_hash
    assert receipt.manifest.focused_checks[0].command.startswith("uv run")
    assert (
        (candidate.candidate_root / PATH)
        .read_text(encoding="utf-8")
        .startswith("Check every")
    )


def test_transport_cannot_escape_scope_or_skip_focused_checks(tmp_path: Path) -> None:
    candidate = workspace(tmp_path)
    with pytest.raises(ValueError, match="target paths"):
        apply_proposal(
            request(candidate.candidate_parent_hash),
            candidate,
            lambda _: draft("src/syndicate/judge_contracts.py").model_dump_json(),
            lambda _, __: True,
        )
    with pytest.raises(ValueError, match="focused checks"):
        apply_proposal(
            request(candidate.candidate_parent_hash),
            candidate,
            lambda _: draft().model_dump_json(),
            lambda _, __: False,
        )


def test_workspace_must_match_the_diagnosed_incumbent(tmp_path: Path) -> None:
    candidate = workspace(tmp_path)
    with pytest.raises(ValueError, match="incumbent"):
        apply_proposal(
            request("d" * 64),
            candidate,
            lambda _: draft().model_dump_json(),
            lambda _, __: True,
        )


def test_symlink_swap_cannot_write_outside_candidate(tmp_path: Path) -> None:
    candidate = workspace(tmp_path)
    external = tmp_path / "outside.txt"
    external.write_text("outside", encoding="utf-8")
    target = candidate.candidate_root / PATH
    target.unlink()
    target.symlink_to(external)

    with pytest.raises(CandidateValidationError, match="symlink"):
        apply_proposal(
            request(candidate.candidate_parent_hash),
            candidate,
            lambda _: draft().model_dump_json(),
            lambda _, __: True,
        )

    assert external.read_text(encoding="utf-8") == "outside"
