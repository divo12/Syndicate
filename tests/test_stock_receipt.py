from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, TimingInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from syndicate.adapters.harbor_agent import CleanupReceipt
from syndicate.services import stock
from syndicate.services.benchmark import RunOutcome
from syndicate.services.stock import (
    AGENT_IMPORT,
    AGENT_NAME,
    CleanupControlReceipt,
    ControllerTrialBinding,
    _controller_authority,
    _write_settled_cleanup,
    load_cleanup_receipt,
    postprocess_stock_result,
)

ENDED = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)


def binding() -> ControllerTrialBinding:
    return ControllerTrialBinding(
        operation_id=uuid4(), attempt_id=uuid4(), run_id=uuid4(), task_id="task-a-1"
    )


def result(identity: ControllerTrialBinding) -> TrialResult:
    return TrialResult(
        id=identity.run_id,
        task_name="owner/task-a-1",
        trial_name="trial-a",
        trial_uri="file:///trials/trial-a",
        task_id=LocalTaskId(path=Path("task-a-1")),
        task_checksum="checksum",
        config=TrialConfig(task=TaskConfig(path=Path("task-a-1"))),
        agent_info=AgentInfo(name="syndicate-nexau", version="0.1.0"),
        agent_execution=TimingInfo(
            started_at=ENDED - timedelta(seconds=5), finished_at=ENDED
        ),
        verifier=TimingInfo(
            started_at=ENDED + timedelta(seconds=1),
            finished_at=ENDED + timedelta(seconds=2),
        ),
        verifier_result=VerifierResult(rewards={"reward": 0}),
    )


def cleanup(identity: ControllerTrialBinding) -> CleanupControlReceipt:
    receipt = CleanupControlReceipt(
        operation_id=identity.operation_id,
        attempt_id=identity.attempt_id,
        run_id=identity.run_id,
        task_id=identity.task_id,
        cleanup=CleanupReceipt(uid=10001, complete=True),
        agent_import=AGENT_IMPORT,
        agent_name=AGENT_NAME,
        uid=10001,
        written_at=ENDED - timedelta(seconds=1),
    )
    return receipt.model_copy(update={"controller_seal": stock._cleanup_seal(receipt)})


def test_receipt_publication_is_exclusive_and_postprocesses(tmp_path: Path) -> None:
    identity = binding()
    expected = cleanup(identity)
    receipt = _write_settled_cleanup(
        _controller_authority(identity, tmp_path), expected.cleanup, expected.written_at
    )
    loaded = load_cleanup_receipt(identity, tmp_path)
    terminal = postprocess_stock_result(
        identity, loaded, result(identity), "harbor:run"
    )
    assert receipt == loaded
    assert terminal.outcome is RunOutcome.FAIL
    assert terminal.verifier.reward == 0.0
    assert terminal.verifier.raw_result_ref == "harbor:run"
    with pytest.raises(FileExistsError):
        _write_settled_cleanup(
            _controller_authority(identity, tmp_path), expected.cleanup, ENDED
        )
    assert load_cleanup_receipt(identity, tmp_path) == expected
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["cleanup.json"]


def test_failed_write_leaves_no_partial_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = binding()

    def fail_sync(descriptor: int) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr("syndicate.services.stock.os.fsync", fail_sync)
    with pytest.raises(OSError, match="disk failed"):
        _write_settled_cleanup(
            _controller_authority(identity, tmp_path), cleanup(identity).cleanup, ENDED
        )
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_fabricated_or_tampered_receipt_fails_closed(tmp_path: Path) -> None:
    identity = binding()
    _write_settled_cleanup(
        _controller_authority(identity, tmp_path), cleanup(identity).cleanup, ENDED
    )
    path = next(tmp_path.rglob("cleanup.json"))
    forged = cleanup(identity).model_copy(update={"controller_seal": "forged"})
    path.write_text(forged.model_dump_json(), encoding="utf-8")
    with pytest.raises(ValueError, match="authentic"):
        load_cleanup_receipt(identity, tmp_path)


def test_symlinked_receipt_path_fails_closed(tmp_path: Path) -> None:
    identity = binding()
    receipt_dir = tmp_path / str(identity.operation_id)
    receipt_dir.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        load_cleanup_receipt(identity, tmp_path)


def test_context_mismatch_fails_closed(tmp_path: Path) -> None:
    identity = binding()
    _write_settled_cleanup(
        _controller_authority(identity, tmp_path), cleanup(identity).cleanup, ENDED
    )
    wrong_context = identity.model_copy(update={"environment_context_id": "other"})
    with pytest.raises(ValueError, match="authentic"):
        load_cleanup_receipt(wrong_context, tmp_path)


@pytest.mark.parametrize("field", ["operation_id", "attempt_id", "run_id", "task_id"])
def test_rejects_cleanup_identity(field: str) -> None:
    identity = binding()
    receipt = cleanup(identity)
    invalid = receipt.model_copy(
        update={field: "different" if field == "task_id" else uuid4()}
    )
    with pytest.raises(ValueError, match="authentic"):
        postprocess_stock_result(identity, invalid, result(identity), "harbor:run")


@pytest.mark.parametrize("field", ["run", "task", "adapter", "missing_result"])
def test_rejects_stock_identity_and_missing_result(field: str) -> None:
    identity = binding()
    invalid = result(identity)
    match field:
        case "run":
            invalid.id = uuid4()
        case "task":
            invalid.task_name = "other"
        case "adapter":
            invalid.agent_info.name = "other"
        case _:
            invalid.verifier_result = None
    with pytest.raises(ValueError):
        postprocess_stock_result(identity, cleanup(identity), invalid, "harbor:run")


@pytest.mark.parametrize("offset", [-6, 1, 3])
def test_rejects_cleanup_outside_agent_interval(offset: int) -> None:
    identity = binding()
    invalid = cleanup(identity).model_copy(
        update={"written_at": ENDED + timedelta(seconds=offset)}
    )
    invalid = invalid.model_copy(
        update={"controller_seal": stock._cleanup_seal(invalid)}
    )
    with pytest.raises(ValueError, match="after agent cleanup"):
        postprocess_stock_result(identity, invalid, result(identity), "harbor:run")


@pytest.mark.parametrize("phase", ["agent_execution", "verifier"])
@pytest.mark.parametrize("defect", ["missing", "unfinished", "reversed", "naive"])
def test_rejects_invalid_intervals(phase: str, defect: str) -> None:
    identity = binding()
    invalid = result(identity)
    timing = TimingInfo(started_at=ENDED, finished_at=ENDED)
    match defect:
        case "unfinished":
            timing.finished_at = None
        case "reversed":
            timing.started_at = ENDED + timedelta(seconds=1)
        case "naive":
            timing.started_at = ENDED.replace(tzinfo=None)
    if phase == "agent_execution":
        invalid.agent_execution = None if defect == "missing" else timing
    else:
        invalid.verifier = None if defect == "missing" else timing
    with pytest.raises(ValueError, match="timing"):
        postprocess_stock_result(identity, cleanup(identity), invalid, "harbor:run")


def test_rejects_unsettled_cleanup_and_naive_proof(tmp_path: Path) -> None:
    identity = binding()
    with pytest.raises(ValueError, match="settled"):
        _write_settled_cleanup(
            _controller_authority(identity, tmp_path),
            CleanupReceipt(uid=10001, complete=False),
            ENDED,
        )
    with pytest.raises(ValueError, match="timezone"):
        _write_settled_cleanup(
            _controller_authority(identity, tmp_path),
            cleanup(identity).cleanup,
            ENDED.replace(tzinfo=None),
        )
