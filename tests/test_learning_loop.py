from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from syndicate.models.candidate import CandidateWorkspace
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
from syndicate.models.jobs import (
    Job,
    JobSubmission,
    StopReason,
    TaskOutcome,
    TaskResult,
)
from syndicate.models.judging import (
    Finding,
    FindingCategory,
    ReportStatus,
    RunCoverage,
    TaskReport,
)
from syndicate.repositories.jobs import SqliteJobStore
from syndicate.services.candidate import create_candidate_workspace
from syndicate.services.executors import SimulatedExecutor
from syndicate.services.improvement import apply_proposal
from syndicate.services.learning_loop import _select, run_outer_loop
from syndicate.services.lineage import HarnessLineage


def _job(tmp_path: Path) -> Job:
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    return store.create(
        JobSubmission(
            task_ids=("regex-log", "extract-elf"), max_iterations=3, patience=2
        )
    )


def test_outer_loop_accepts_on_strict_improvement_and_stops(tmp_path: Path) -> None:
    receipt = run_outer_loop(_job(tmp_path))
    assert receipt.stop_reason is StopReason.ALL_TASKS_PASSED
    assert receipt.best_score == 1
    assert receipt.iterations[0].accepted is True
    assert receipt.iterations[1].accepted is True


def test_outer_loop_accepts_a_single_task_from_a_zero_baseline(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    job = store.create(
        JobSubmission(task_ids=("regex-log",), max_iterations=3, patience=2)
    )
    receipt = run_outer_loop(job)
    assert receipt.stop_reason is StopReason.ALL_TASKS_PASSED
    assert receipt.best_score == 1
    assert receipt.iterations[1].accepted is True


def test_outer_loop_uses_assess_comparison_and_lineage(tmp_path: Path) -> None:
    digest = f"sha256:{0:064x}"
    lineage = HarnessLineage(tmp_path / "lineage.sqlite", digest, digest)
    job = _job(tmp_path)
    incumbent = SimulatedExecutor().run(job.task_ids, 0)
    candidate = SimulatedExecutor().run(job.task_ids, 1)
    assert _select(incumbent, candidate, job, lineage, 0, 1) is True
    assert lineage.current().harness_hash == f"sha256:{1:064x}"


def test_infra_error_is_not_a_verified_failure(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    job = store.create(JobSubmission(task_ids=("regex-log",), max_iterations=2))
    infra = (
        TaskResult(task_id="regex-log", outcome=TaskOutcome.INFRA_ERROR, reward=0.0),
    )
    assert _select(infra, infra, job, None, 0, 1) is False


def test_rejected_generation_is_not_used_as_incumbent(tmp_path: Path) -> None:
    digest = f"sha256:{0:064x}"
    lineage = HarnessLineage(tmp_path / "lineage.sqlite", digest, digest)
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    job = store.create(
        JobSubmission(
            task_ids=("regex-log", "extract-elf"), max_iterations=4, patience=3
        )
    )

    def executor(task_ids: tuple[str, ...], generation: int) -> tuple[TaskResult, ...]:
        return SimulatedExecutor().run(task_ids, 1 if generation >= 2 else 0)

    receipt = run_outer_loop(job, executor=executor, lineage=lineage)
    assert receipt.iterations[1].accepted is False
    assert receipt.iterations[2].accepted is True
    assert lineage.current().harness_hash == f"sha256:{2:064x}"


def test_improve_port_can_call_apply_proposal(tmp_path: Path) -> None:
    path = "harnesses/seed/systemprompt.md"
    incumbent = tmp_path / "incumbent"
    target = incumbent / path
    target.parent.mkdir(parents=True)
    target.write_text("Check the available operations.\n", encoding="utf-8")
    workspace: CandidateWorkspace = create_candidate_workspace(
        incumbent, (path,), tmp_path / "workspaces"
    )
    run = UUID(int=1)
    citation = SpanCitation(run_id=run, trace_ref="a" * 32, span_ref="b" * 16)
    report = TaskReport(
        task_id="task-a-1",
        judge_spec_hash="c" * 64,
        run_ids=(run,),
        status=ReportStatus.COMPLETE,
        findings=(
            Finding(
                finding_id="finding-1",
                category=FindingCategory.MISSED_RECORDS,
                observation="The agent stopped after one response page.",
                hypothesis="Pagination guidance may be missing.",
                evidence=(citation,),
            ),
        ),
        coverage=(RunCoverage(run_id=run, examined=(citation,)),),
    )
    diagnosis = FailureDiagnosis(
        diagnosis_id="diagnosis-1",
        campaign_id="campaign-1",
        parent_harness_hash="sha256:" + workspace.candidate_parent_hash,
        reports=(report,),
        evidence_refs=(citation,),
        observed_pattern="Runs stop after the first response page.",
        competing_hypotheses=(
            "Pagination guidance is missing.",
            "The provider has no second page.",
        ),
        root_cause_hypothesis="Pagination guidance is missing.",
        edit_scope=EditScope(allowed_paths=(path,), target_paths=(path,)),
    )
    request = ProposalRequest(
        candidate_id="candidate-1",
        diagnosis=diagnosis,
        usage_reservation_ref="reservation-1",
        focused_checks=("true",),
        submitted_at=datetime(2026, 9, 6, tzinfo=UTC),
    )
    draft = ProposalDraft(
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
    called: list[int] = []

    def improve(generation: int) -> int:
        apply_proposal(
            request,
            workspace,
            lambda _: draft.model_dump_json(),
            lambda _workspace, _command: True,
        )
        called.append(generation)
        return generation + 1

    receipt = run_outer_loop(_job(tmp_path), improve=improve)
    assert called == [0]
    assert receipt.stop_reason is StopReason.ALL_TASKS_PASSED
