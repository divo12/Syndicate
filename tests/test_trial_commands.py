from pathlib import Path
from uuid import UUID

import pytest

from syndicate.cli import main
from syndicate.models.budget import BudgetCap
from syndicate.models.commands import JudgeTaskCommand, RunTrialCommand
from syndicate.models.envelope import CommandStatus
from syndicate.models.jobs import ExecutorKind, JobSubmission, TaskResult
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.repositories.jobs import MemoryJobStore
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
            budget=_budget(),
        ),
        store,
    )
    assert judged.status is CommandStatus.COMPLETED


def test_harbor_executor_fails_closed_without_credentials() -> None:
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


def test_harbor_job_is_marked_failed_without_keys() -> None:
    store = MemoryJobStore()
    store.create(JobSubmission(task_ids=("regex-log",), executor=ExecutorKind.HARBOR))

    class Idle:
        def start_loop(self, job: object) -> None:
            del job
            return None

    finished = JobWorker(store, Idle()).process_one()
    assert finished is not None
    assert finished.status.value == "failed"
    assert finished.error is not None
