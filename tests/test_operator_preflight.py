"""Operator entry point provisions its own internal request and trust snapshot."""

from pathlib import Path

import pytest
import test_preflight as preflight_tests

from syndicate.cli import main
from syndicate.cli_envelope import CommandReceipt, CommandStatus, PreflightCommand
from syndicate.preflight import ControllerConfig, PreflightConfig, PreflightResult

checkout = preflight_tests.checkout
controller = preflight_tests.controller


@pytest.fixture
def campaign_file(
    controller: ControllerConfig, monkeypatch: pytest.MonkeyPatch
) -> Path:
    root = controller.env_file.parent
    monkeypatch.chdir(root)
    config = PreflightConfig(
        env_file=Path(controller.env_file.name),
        benchmark_root=controller.benchmark_root,
        assignments=controller.assignments,
        budget=controller.budget,
    )
    path = root / "campaign.json"
    path.write_text(config.model_dump_json())
    return path


def test_operator_provisions_independent_runs(
    campaign_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = []
    for _ in range(2):
        assert main(["preflight", "--config", str(campaign_file)]) == 0
        output = capsys.readouterr()
        receipt = CommandReceipt.model_validate_json(output.out)
        assert receipt.status == CommandStatus.COMPLETED
        run = (
            Path(".syndicate/runs")
            / str(receipt.operation_id)
            / str(receipt.attempt_id)
        )
        request = PreflightCommand.model_validate_json(
            (run / "request.json").read_bytes()
        )
        snapshot = ControllerConfig.model_validate_json(
            (run / "controller.json").read_bytes()
        )
        result = PreflightResult.model_validate_json(
            (run / "preflight.json").read_bytes()
        )
        assert request.content_hash in snapshot.approved_request_hashes
        assert request.operation_id == result.operation_id == receipt.operation_id
        assert snapshot.env_file.is_absolute()
        assert (
            "never-print"
            not in output.out + output.err + (run / "controller.json").read_text()
        )
        runs.append(run)
    assert runs[0] != runs[1]
    assert not Path(".syndicate/controller.json").exists()


@pytest.mark.parametrize("fault", ["budget", "extra", "secret"])
def test_operator_rejects_invalid_configuration(
    campaign_file: Path, fault: str, capsys: pytest.CaptureFixture[str]
) -> None:
    text = campaign_file.read_text()
    if fault == "budget":
        text = text.replace('"max_tokens":100', '"max_tokens":0')
    elif fault == "extra":
        text = text.replace("{", '{"approved_request_hashes":[],', 1)
    else:
        text = "never-print-this-secret"
    campaign_file.write_text(text)
    assert main(["preflight", "--config", str(campaign_file)]) == 2
    output = capsys.readouterr()
    receipt = CommandReceipt.model_validate_json(output.out)
    assert receipt.status == CommandStatus.BLOCKED
    assert "never-print" not in output.out + output.err
    assert not Path(".syndicate/runs").exists()


def test_operator_rejects_symlink_output(
    campaign_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = campaign_file.parent / "outside"
    outside.mkdir()
    Path(".syndicate").symlink_to(outside, target_is_directory=True)
    assert main(["preflight", "--config", str(campaign_file)]) != 0
    assert not tuple(outside.iterdir())
    assert (
        CommandReceipt.model_validate_json(capsys.readouterr().out).artifact_refs == ()
    )
