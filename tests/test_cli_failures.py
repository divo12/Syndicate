"""Terminal failures retain their classification, IDs, and safe messages."""

from pathlib import Path
from uuid import UUID

import pytest
import test_benchmark_manifest as benchmark_tests
import test_preflight as fixtures
from pydantic import ValidationError

from syndicate.cli import main
from syndicate.models.envelope import CommandReceipt, ErrorReason
from syndicate.services.preflight import ControllerConfig

checkout = fixtures.checkout
controller = fixtures.controller
request_path = fixtures.request_path


@pytest.mark.parametrize("fault", ["corrupt", "missing", "dirty", "revision"])
def test_infrastructure_failure(
    request_path: Path,
    controller: ControllerConfig,
    fault: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    anchor = Path(".syndicate/controller.json")
    if fault == "corrupt":
        anchor.write_text("secret-truncated-json")
    elif fault == "missing":
        anchor.unlink()
    elif fault == "dirty":
        (controller.benchmark_root / "untracked").touch()
    else:
        benchmark_tests.git(controller.benchmark_root, "checkout", "--orphan", "empty")
    assert main(["execute", "--request", str(request_path)]) == 1
    output = capsys.readouterr()
    receipt = CommandReceipt.model_validate_json(output.out)
    assert receipt.operation_id == UUID(int=1)
    assert receipt.error is not None
    assert receipt.error.reason == ErrorReason.INFRASTRUCTURE
    assert receipt.error.message == "Runtime infrastructure failure"
    assert "secret" not in output.out + output.err


def test_admission_precedes_benchmark_access(
    request_path: Path,
    controller: ControllerConfig,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (controller.benchmark_root / "untracked").touch()
    request_path.write_text(
        request_path.read_text().replace(controller.approved_manifest_hash, "0" * 64)
    )
    assert main(["execute", "--request", str(request_path)]) == 2
    receipt = CommandReceipt.model_validate_json(capsys.readouterr().out)
    assert receipt.error is not None
    assert receipt.error.reason == ErrorReason.INVALID_REQUEST


def test_unavailable_cwd_emits_receipt(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable() -> Path:
        raise OSError("secret-path")

    monkeypatch.setattr(Path, "cwd", unavailable)
    assert main(["preflight", "--config", "campaign.json"]) == 1
    output = capsys.readouterr()
    receipt = CommandReceipt.model_validate_json(output.out)
    assert receipt.operation_id is None
    assert receipt.error is not None
    assert receipt.error.reason == ErrorReason.INFRASTRUCTURE
    assert "secret-path" not in output.out + output.err


@pytest.mark.parametrize("status", ["completed", "failed", "blocked", "cancelled"])
def test_receipt_rejects_missing_outcome(status: str) -> None:
    with pytest.raises(ValidationError):
        CommandReceipt.model_validate_json(
            '{"operation_id":null,"attempt_id":null,"status":"' + status + '"}'
        )


@pytest.mark.parametrize("fault", ["ids", "null", "error", "duplicate"])
def test_receipt_rejects_inconsistent_success(fault: str) -> None:
    artifact = {
        "operation_id": str(UUID(int=1)),
        "attempt_id": str(UUID(int=2)),
        "sha256": "a" * 64,
    }
    receipt = {
        "operation_id": artifact["operation_id"],
        "attempt_id": artifact["attempt_id"],
        "status": "completed",
        "artifact_refs": [artifact],
    }
    import json

    raw = json.dumps(receipt)
    if fault == "ids":
        raw = raw.replace(str(UUID(int=1)), str(UUID(int=3)), 1)
    elif fault == "null":
        raw = raw.replace('"' + str(UUID(int=1)) + '"', "null", 1)
    elif fault == "error":
        raw = raw[:-1] + ',"error":{"reason":"infrastructure","message":"failed"}}'
    else:
        receipt["artifact_refs"] = [artifact, artifact]
        raw = json.dumps(receipt)
    with pytest.raises(ValidationError):
        CommandReceipt.model_validate_json(raw)
