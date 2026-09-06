from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr

from syndicate.adapters.harbor_agent import CleanupReceipt
from syndicate.cli import main
from syndicate.controllers.handler_inputs import RuntimeInput
from syndicate.controllers.live_handlers import LiveHandlers
from syndicate.models.commands import RunTrialCommand
from syndicate.models.envelope import ArtifactKind, CommandReceipt, CommandStatus
from syndicate.models.jobs import TaskOutcome, TaskResult
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.repositories.benchmark_manifest import Assignment
from syndicate.services.benchmark import (
    RunOutcome,
    RunReceipt,
    VerifierReason,
    VerifierReceipt,
)
from syndicate.services.executors import HarborExecutor


def _azure_env(path: Path) -> Path:
    path.write_text(
        "AZURE_OPENAI_API_KEY=test-secret\n"
        "AZURE_OPENAI_BASE_URL=https://azure.example/openai/v1/\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini\n"
    )
    return path


def _bind_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "e2b-test")
    monkeypatch.delenv("SYNDICATE_HARBOR_STUB", raising=False)
    monkeypatch.setenv(
        "SYNDICATE_ENV_FILE", str(_azure_env(tmp_path / "controller.env"))
    )
    monkeypatch.setenv("ARTIFACT_ROOT", str((tmp_path / "artifacts").resolve()))
    bench = tmp_path / "bench"
    task = bench / "tasks" / "task-a-1"
    task.mkdir(parents=True)
    (task / "instruction.md").write_text("Reset the ServiceNow password.\n")
    monkeypatch.setenv("BENCHMARK_ROOT", str(bench))


def test_harbor_executor_binds_live_run_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_env(tmp_path, monkeypatch)

    def fake_run(
        command: RunTrialCommand,
        store: ArtifactStore,
        key: SecretStr,
        handlers: LiveHandlers,
        benchmark_root: Path,
        assignments: tuple[Assignment, ...],
    ) -> CommandReceipt:
        del key, handlers
        assert benchmark_root.name == "bench"
        assert tuple(item.task_id for item in assignments) == ("task-a-1",)
        receipt = RunReceipt(
            operation_id=command.operation_id,
            attempt_id=command.attempt_id,
            run_id=UUID(int=9),
            task_id="task-a-1",
            cleanup=CleanupReceipt(complete=True),
            outcome=RunOutcome.PASS,
            verifier=VerifierReceipt(
                outcome=RunOutcome.PASS,
                reason=VerifierReason.PASSED,
                reward=1.0,
                raw_result_ref="harbor:bound",
            ),
        )
        reference = store.write(command, ArtifactKind.RUN, receipt)
        return CommandReceipt(
            operation_id=command.operation_id,
            attempt_id=command.attempt_id,
            status=CommandStatus.COMPLETED,
            artifact_refs=(reference,),
        )

    monkeypatch.setattr("syndicate.services.harbor_dispatch.run", fake_run)
    results = HarborExecutor().run(("task-a-1",), 0)
    assert [item.outcome for item in results] == [TaskOutcome.PASSED]
    assert results[0].reward == 1.0


def test_generation_appends_mined_lessons_to_system_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _bind_env(tmp_path, monkeypatch)
    lessons = tmp_path / "artifacts" / "harnesses" / "gen-1.lessons.md"
    lessons.parent.mkdir(parents=True)
    lessons.write_text("Fix the Okta user named Tendai.\n", encoding="utf-8")
    captured: dict[str, str] = {}

    def fake_run(
        command: RunTrialCommand,
        store: ArtifactStore,
        key: SecretStr,
        handlers: LiveHandlers,
        benchmark_root: Path,
        assignments: tuple[Assignment, ...],
    ) -> CommandReceipt:
        del key, handlers, benchmark_root, assignments
        payload = store.load(command.runtime_request_ref, RuntimeInput)
        captured["prompt"] = payload.request.baseline.rendered_prompt
        receipt = RunReceipt(
            operation_id=command.operation_id,
            attempt_id=command.attempt_id,
            run_id=UUID(int=3),
            task_id="task-a-1",
            cleanup=CleanupReceipt(complete=True),
            outcome=RunOutcome.FAIL,
            verifier=VerifierReceipt(
                outcome=RunOutcome.FAIL,
                reason=VerifierReason.FAILED,
                reward=0.0,
                raw_result_ref="harbor:bound",
            ),
        )
        reference = store.write(command, ArtifactKind.RUN, receipt)
        return CommandReceipt(
            operation_id=command.operation_id,
            attempt_id=command.attempt_id,
            status=CommandStatus.COMPLETED,
            artifact_refs=(reference,),
        )

    monkeypatch.setattr("syndicate.services.harbor_dispatch.run", fake_run)
    HarborExecutor().run(("task-a-1",), 1)
    assert "Fix the Okta user named Tendai." in captured["prompt"]


def test_harbor_executor_fails_closed_without_azure_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("E2B_API_KEY", "e2b-test")
    monkeypatch.delenv("SYNDICATE_HARBOR_STUB", raising=False)
    monkeypatch.setenv("SYNDICATE_ENV_FILE", str(tmp_path / "missing.env"))
    monkeypatch.setenv("ARTIFACT_ROOT", str((tmp_path / "artifacts").resolve()))
    try:
        HarborExecutor().run(("task-a-1",), 0)
    except ValueError as error:
        assert "Harbor" in str(error) or "model" in str(error).lower()
    else:
        raise AssertionError("unbound azure config must fail closed")


def test_trial_cli_uses_bound_harbor_when_e2b_is_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _bind_env(tmp_path, monkeypatch)
    monkeypatch.setattr(
        HarborExecutor,
        "run",
        lambda self, task_ids, generation: (
            TaskResult(task_id=task_ids[0], outcome=TaskOutcome.PASSED, reward=1.0),
        ),
    )
    assert main(["trial", "--task-id", "task-a-1", "--generation", "0"]) == 0
    printed = TaskResult.model_validate_json(capsys.readouterr().out.strip())
    assert printed.outcome is TaskOutcome.PASSED
