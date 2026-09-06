from pathlib import Path
from uuid import UUID

import pytest

from syndicate.cli import main
from syndicate.models.budget import BudgetCap
from syndicate.models.commands import JudgeTaskCommand, RunTrialCommand
from syndicate.models.envelope import ArtifactKind, ArtifactRef, CommandStatus
from syndicate.models.jobs import ExecutorKind, JobSubmission, TaskResult
from syndicate.models.judging import TaskReport
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.repositories.jobs import SqliteJobStore
from syndicate.services.benchmark import RunOutcome, VerifierReason, VerifierReceipt
from syndicate.services.executors import HarborExecutor, task_result_from_verifier
from syndicate.services.job_worker import JobWorker
from syndicate.services.trial_commands import execute_judge_task, execute_run_trial


def _budget() -> BudgetCap:
    return BudgetCap(max_tokens=1, max_seconds=1, max_spend_microusd=1)


def test_run_trial_and_judge_task_handlers_are_installed(tmp_path: Path) -> None:
    root = tmp_path / ".syndicate"
    root.mkdir()
    store = ArtifactStore(root.resolve())
    digest = "a" * 64
    trial = RunTrialCommand(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        task_id="regex-log",
        harness_hash=digest,
        memory_hash=digest,
        model_config_hash=digest,
        runtime_image_hash=digest,
        judge_spec_hash=digest,
        verifier_version="1",
        runtime_request_ref=ArtifactRef(
            kind=ArtifactKind.RUNTIME_REQUEST,
            operation_id=UUID(int=1),
            attempt_id=UUID(int=2),
            sha256=digest,
        ),
        budget=_budget(),
    )
    receipt = execute_run_trial(trial, store)
    assert receipt.status is CommandStatus.COMPLETED
    judged = execute_judge_task(
        JudgeTaskCommand(
            operation_id=UUID(int=3),
            attempt_id=UUID(int=4),
            task_id="regex-log",
            judge_spec_hash=digest,
            run_refs=receipt.artifact_refs,
            judge_input_ref=ArtifactRef(
                kind=ArtifactKind.JUDGE_INPUT,
                operation_id=UUID(int=3),
                attempt_id=UUID(int=4),
                sha256=digest,
            ),
            budget=_budget(),
        ),
        store,
    )
    assert judged.status is CommandStatus.COMPLETED
    assert store.load(judged.artifact_refs[0], TaskReport).task_id == "regex-log"


def test_judge_task_rejects_multiple_run_refs(tmp_path: Path) -> None:
    root = tmp_path / ".syndicate"
    root.mkdir()
    store = ArtifactStore(root.resolve())
    digest = "a" * 64
    first = execute_run_trial(
        RunTrialCommand(
            operation_id=UUID(int=1),
            attempt_id=UUID(int=2),
            task_id="regex-log",
            harness_hash=digest,
            memory_hash=digest,
            model_config_hash=digest,
            runtime_image_hash=digest,
            judge_spec_hash=digest,
            verifier_version="1",
            runtime_request_ref=ArtifactRef(
                kind=ArtifactKind.RUNTIME_REQUEST,
                operation_id=UUID(int=1),
                attempt_id=UUID(int=2),
                sha256=digest,
            ),
            budget=_budget(),
        ),
        store,
    )
    extra = first.artifact_refs[0]
    try:
        execute_judge_task(
            JudgeTaskCommand(
                operation_id=UUID(int=3),
                attempt_id=UUID(int=4),
                task_id="regex-log",
                judge_spec_hash=digest,
                run_refs=(extra, extra),
                judge_input_ref=ArtifactRef(
                    kind=ArtifactKind.JUDGE_INPUT,
                    operation_id=UUID(int=3),
                    attempt_id=UUID(int=4),
                    sha256=digest,
                ),
                budget=_budget(),
            ),
            store,
        )
    except ValueError as error:
        assert "exactly one" in str(error)
    else:
        raise AssertionError("multiple run refs must fail")


def test_harbor_executor_fails_closed_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("SYNDICATE_HARBOR_STUB", raising=False)
    try:
        HarborExecutor().run(("regex-log",), 0)
    except ValueError as error:
        assert "E2B_API_KEY" in str(error)
    else:
        raise AssertionError("harbor must fail closed")


def test_verifier_receipts_map_onto_task_results() -> None:
    passed = task_result_from_verifier(
        "regex-log",
        VerifierReceipt(
            outcome=RunOutcome.PASS,
            reason=VerifierReason.PASSED,
            reward=1.0,
            raw_result_ref="neatlogs:pass",
        ),
    )
    failed = task_result_from_verifier(
        "regex-log",
        VerifierReceipt(
            outcome=RunOutcome.FAIL,
            reason=VerifierReason.FAILED,
            reward=0.0,
            raw_result_ref="neatlogs:fail",
        ),
    )
    infra = task_result_from_verifier(
        "regex-log",
        VerifierReceipt(
            outcome=RunOutcome.UNVERIFIED,
            reason=VerifierReason.MISSING_RESULT,
            raw_result_ref="neatlogs:missing",
        ),
    )
    assert passed.outcome.value == "passed"
    assert failed.outcome.value == "failed"
    assert infra.outcome.value == "infra_error"


def test_harbor_executor_uses_an_injected_runner() -> None:
    def runner(task_id: str, generation: int) -> TaskResult:
        del generation
        return task_result_from_verifier(
            task_id,
            VerifierReceipt(
                outcome=RunOutcome.PASS,
                reason=VerifierReason.PASSED,
                reward=1.0,
                raw_result_ref="neatlogs:injected",
            ),
        )

    results = HarborExecutor(runner).run(("regex-log", "extract-elf"), 0)
    assert [item.outcome.value for item in results] == ["passed", "passed"]


def test_trial_cli_prints_one_task_result(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["trial", "--task-id", "regex-log", "--generation", "1"]) == 0
    printed = TaskResult.model_validate_json(capsys.readouterr().out.strip())
    assert printed.task_id == "regex-log"
    assert printed.outcome.value == "passed"
    assert main(["trial", "--task-id", " ", "--generation", "0"]) == 2
    assert (
        main(
            [
                "trial",
                "--task-id",
                "regex-log",
                "--generation",
                "0",
                "--failing-task-id",
                "extract-elf",
            ]
        )
        == 0
    )
    other = TaskResult.model_validate_json(capsys.readouterr().out.strip())
    assert other.outcome.value == "passed"


def test_harbor_job_is_marked_failed_without_keys(tmp_path: Path) -> None:
    store = SqliteJobStore(tmp_path / "jobs.sqlite")
    store.create(JobSubmission(task_ids=("regex-log",), executor=ExecutorKind.HARBOR))

    class Idle:
        def start_loop(self, job: object) -> None:
            del job
            return None

    finished = JobWorker(store, Idle()).process_one()
    assert finished is not None
    assert finished.status.value == "failed"
    assert finished.error is not None
