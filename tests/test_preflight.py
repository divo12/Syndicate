"""Public offline preflight and process transport contracts."""

import hashlib
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import test_benchmark_manifest as benchmark_tests
from pydantic import ValidationError
from test_budget_policy import policy

from syndicate.models.envelope import PreflightCommand
from syndicate.models.model_config import load_model_config
from syndicate.services.preflight import ControllerConfig, configuration_hash, preflight

checkout = benchmark_tests.checkout


def test_request_rejects_caller_declared_assignments() -> None:
    raw = (
        '{"schema_version":1,"operation":"preflight",'
        f'"operation_id":"{uuid4()}","attempt_id":"{uuid4()}",'
        '"manifest_hash":"' + "a" * 64 + '","assignments":[]}'
    )
    with pytest.raises(ValidationError):
        PreflightCommand.model_validate_json(raw)


@pytest.fixture
def controller(
    checkout: tuple[Path, str], tmp_path_factory: pytest.TempPathFactory
) -> ControllerConfig:
    manifest = benchmark_tests.load(checkout)
    env = tmp_path_factory.mktemp("controller") / "model.env"
    env.write_text(
        "AZURE_OPENAI_API_KEY=never-print-this-secret\n"
        "AZURE_OPENAI_BASE_URL=https://example.test\n"
        "AZURE_OPENAI_DEPLOYMENT=gpt-5.4-mini\n"
    )
    return ControllerConfig(
        env_file=env,
        benchmark_root=manifest.root,
        assignments=tuple(task.assignment for task in manifest.tasks),
        approved_manifest_hash=manifest.content_hash,
        budget=policy(),
        approved_config_hash=configuration_hash(
            load_model_config(env).settings, policy()
        ),
        approved_request_hashes=(
            PreflightCommand(
                operation_id=UUID(int=1),
                attempt_id=UUID(int=2),
                manifest_hash=manifest.content_hash,
            ).content_hash,
        ),
    )


def command(controller: ControllerConfig) -> PreflightCommand:
    return PreflightCommand(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        manifest_hash=controller.approved_manifest_hash,
    )


def test_preflight_validates_configuration_only(controller: ControllerConfig) -> None:
    result = preflight(command(controller), controller)
    assert result.configuration_valid is True
    assert result.live_model_verified is False
    assert result.manifest_hash == controller.approved_manifest_hash
    assert "never-print" not in result.model_dump_json()
    assert "task-c" not in result.model_dump_json()


def test_rejects_unapproved_registry(controller: ControllerConfig) -> None:
    with pytest.raises(ValueError):
        preflight(
            PreflightCommand(
                operation_id=uuid4(), attempt_id=uuid4(), manifest_hash="0" * 64
            ),
            controller,
        )


def test_rejects_forged_final_split_and_dirty_checkout(
    controller: ControllerConfig,
) -> None:
    from dataclasses import replace

    from syndicate.repositories.benchmark_manifest import Split

    forged = controller.model_copy(
        update={
            "assignments": tuple(
                replace(item, split=Split.DEVELOPMENT)
                for item in controller.assignments
            )
        }
    )
    with pytest.raises(ValueError, match="approved hash"):
        preflight(command(controller), forged)
    (controller.benchmark_root / "untracked").touch()
    with pytest.raises(ValueError, match="Benchmark checkout"):
        preflight(command(controller), controller)


def test_controller_requires_valid_budgets(controller: ControllerConfig) -> None:
    invalid = controller.model_dump_json().replace('"max_tokens":100', '"max_tokens":0')
    with pytest.raises(ValidationError):
        ControllerConfig.model_validate_json(invalid)


def test_model_configuration_failure_is_secret_safe(
    controller: ControllerConfig,
) -> None:
    controller.env_file.write_text(
        "AZURE_OPENAI_API_KEY=never-print-this-secret\n"
        "AZURE_OPENAI_BASE_URL=https://example.test\n"
        "AZURE_OPENAI_DEPLOYMENT=never-print-this-secret\n"
    )
    with pytest.raises(ValueError) as error:
        preflight(command(controller), controller)
    assert "never-print" not in str(error.value)


def test_rejects_unapproved_valid_model_change(controller: ControllerConfig) -> None:
    controller.env_file.write_text(
        controller.env_file.read_text().replace("example.test", "changed.test")
    )
    with pytest.raises(ValueError, match="approved"):
        preflight(command(controller), controller)


@pytest.fixture
def request_path(controller: ControllerConfig, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = controller.env_file.parent
    request = command(controller)
    run = (
        root
        / ".syndicate"
        / "runs"
        / str(request.operation_id)
        / str(request.attempt_id)
    )
    run.mkdir(parents=True)
    (root / ".syndicate/controller.json").write_text(controller.model_dump_json())
    path = run / "request.json"
    path.write_text(request.model_dump_json())
    monkeypatch.chdir(root)
    return path


def test_cli_receipt_matches_artifact(
    controller: ControllerConfig,
    request_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from syndicate.cli import main
    from syndicate.models.envelope import CommandReceipt, CommandStatus

    request = command(controller)
    path = request_path
    assert main(["execute", "--request", str(path)]) == 0
    output = capsys.readouterr()
    receipt = CommandReceipt.model_validate_json(output.out)
    assert receipt.status == CommandStatus.COMPLETED
    assert receipt.operation_id == request.operation_id
    assert receipt.attempt_id == request.attempt_id
    assert len(output.out.splitlines()) == 1
    assert len(receipt.artifact_refs) == 1
    assert "never-print" not in output.out + output.err


def test_artifact_hash_and_ids_match_receipt(
    request_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from syndicate.cli import main
    from syndicate.models.envelope import CommandReceipt
    from syndicate.services.preflight import PreflightResult

    assert main(["execute", "--request", str(request_path)]) == 0
    receipt = CommandReceipt.model_validate_json(capsys.readouterr().out)
    run = request_path.parent
    payload = (run / "preflight.json").read_bytes()
    result = PreflightResult.model_validate_json(payload)
    assert result.operation_id == receipt.operation_id
    assert result.attempt_id == receipt.attempt_id
    reference = receipt.artifact_refs[0]
    assert reference.operation_id == receipt.operation_id
    assert reference.attempt_id == receipt.attempt_id
    assert reference.sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize("fault", ["outside", "symlink", "ids", "unapproved", "output"])
def test_cli_rejects_boundary_failures(
    request_path: Path, fault: str, capsys: pytest.CaptureFixture[str]
) -> None:
    from syndicate.cli import main
    from syndicate.models.envelope import CommandReceipt, CommandStatus

    path = request_path
    if fault == "outside":
        path = Path.cwd() / "request.json"
        path.write_bytes(request_path.read_bytes())
    elif fault == "symlink":
        path = request_path.with_name("link.json")
        path.symlink_to(request_path)
    elif fault == "ids":
        path.write_text(path.read_text().replace(str(UUID(int=1)), str(UUID(int=3))))
    elif fault == "unapproved":
        anchor = Path.cwd() / ".syndicate/controller.json"
        config = ControllerConfig.model_validate_json(anchor.read_bytes())
        anchor.write_text(
            config.model_copy(update={"approved_request_hashes": ()}).model_dump_json()
        )
    else:
        (path.parent / "preflight.json").write_text("preserve")
    exit_code = main(["execute", "--request", str(path)])
    output = capsys.readouterr()
    receipt = CommandReceipt.model_validate_json(output.out)
    assert exit_code == (1 if fault == "output" else 2)
    assert receipt.status != CommandStatus.COMPLETED
    assert receipt.artifact_refs == ()
    assert "never-print" not in output.out + output.err


@pytest.mark.parametrize(
    "raw", ["never-print-this-secret", '{"operation":"run-trial"}']
)
def test_module_emits_one_safe_receipt_for_invalid_request(
    request_path: Path, raw: str
) -> None:
    from syndicate.models.envelope import CommandReceipt, ErrorReason

    request_path.write_text(raw)
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "syndicate.cli",
            "execute",
            "--request",
            str(request_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    receipt = CommandReceipt.model_validate_json(process.stdout)
    assert process.returncode == 2
    assert receipt.error is not None
    assert receipt.error.reason == ErrorReason.INVALID_REQUEST
    assert "never-print" not in process.stdout + process.stderr
