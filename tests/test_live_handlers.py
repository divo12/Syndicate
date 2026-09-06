"""Controller defaults must be installed even without external Harbor state."""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from pydantic import SecretStr
from test_runtime_request import request as runtime_request

from syndicate.controllers.handler_inputs import RuntimeInput
from syndicate.controllers.live_handlers import _run_check
from syndicate.controllers.preflight import dispatch
from syndicate.models.budget import BudgetCap
from syndicate.models.candidate import CandidateWorkspace, IncumbentSnapshot
from syndicate.models.commands import RunTrialCommand
from syndicate.models.envelope import ArtifactKind, ArtifactRef, CommandStatus
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.repositories.benchmark_manifest import Assignment, Split


def command(reference: ArtifactRef) -> RunTrialCommand:
    return RunTrialCommand(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        task_id="task-a-1",
        harness_hash="a" * 64,
        memory_hash="b" * 64,
        model_config_hash="c" * 64,
        runtime_image_hash="d" * 64,
        judge_spec_hash="e" * 64,
        verifier_version="v1",
        runtime_request_ref=reference,
        budget=BudgetCap(max_tokens=50_000, max_seconds=30, max_spend_microusd=100_000),
    )


def test_default_trial_handler_returns_typed_blocked_without_harbor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = (tmp_path / ".syndicate").resolve()
    root.mkdir()
    placeholder = ArtifactRef(
        kind=ArtifactKind.RUNTIME_REQUEST,
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        sha256="f" * 64,
    )
    store = ArtifactStore(root)
    reference = store.write(
        command(placeholder),
        ArtifactKind.RUNTIME_REQUEST,
        RuntimeInput(request=runtime_request()),
    )
    value = command(reference)
    controller = SimpleNamespace(
        approved_request_hashes=(value.content_hash,),
        budget=SimpleNamespace(budget_for=lambda _: value.budget),
        env_file=tmp_path / "controller.env",
        benchmark_root=tmp_path,
        assignments=(Assignment("task-a-1", Split.DEVELOPMENT, "family"),),
    )
    (root / "controller.json").write_text("{}")
    monkeypatch.setattr(
        "syndicate.controllers.preflight.ControllerConfig.model_validate_json",
        lambda _: controller,
    )
    monkeypatch.setattr(
        "syndicate.controllers.preflight.load_model_config",
        lambda _: SimpleNamespace(api_key=SecretStr("key")),
    )

    request_path = root / "runs" / str(value.operation_id) / str(value.attempt_id)
    request_path.mkdir(parents=True, exist_ok=True)
    request = request_path / "request.json"
    request.write_text(value.model_dump_json())
    monkeypatch.chdir(tmp_path)

    receipt, exit_code = dispatch(["execute", "--request", str(request)])

    assert exit_code == 0
    assert receipt.status is CommandStatus.BLOCKED


def test_candidate_check_uses_fixed_argv_and_candidate_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    workspace = CandidateWorkspace(
        root=tmp_path,
        snapshot_root=tmp_path,
        candidate_root=candidate,
        incumbent=IncumbentSnapshot(root=tmp_path, content_hash="a" * 64),
        candidate_parent_hash="a" * 64,
        allowed_paths=(),
    )
    calls: list[tuple[str, ...]] = []

    def run(arguments: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
        calls.append(arguments)
        assert kwargs.get("cwd") == candidate
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("syndicate.controllers.live_handlers.subprocess.run", run)

    assert _run_check(workspace, "uv run pytest test_candidate.py")
    assert calls == [("uv", "run", "pytest", "test_candidate.py")]
    with pytest.raises(ValueError, match="explicit"):
        _run_check(workspace, "sh -c unsafe")
    with pytest.raises(ValueError, match="candidate-relative"):
        _run_check(workspace, "uv run pytest --basetemp=/tmp/x")
