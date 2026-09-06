from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from harbor.models.task.id import LocalTaskId
from harbor.models.trial.config import TaskConfig, TrialConfig
from harbor.models.trial.result import AgentInfo, TimingInfo, TrialResult
from harbor.models.verifier.result import VerifierResult

from syndicate.benchmark import RunOutcome
from syndicate.harbor_agent import CleanupReceipt
from syndicate.stock_receipt import (
    CleanupControlReceipt,
    ControllerTrialBinding,
    emit_cleanup_receipt,
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
    return CleanupControlReceipt(
        operation_id=identity.operation_id,
        attempt_id=identity.attempt_id,
        run_id=identity.run_id,
        task_id=identity.task_id,
        cleanup=CleanupReceipt(uid=10001, complete=True),
        written_at=ENDED - timedelta(seconds=1),
    )


def test_receipt_publication_is_exclusive_and_postprocesses(tmp_path: Path) -> None:
    identity = binding()
    expected = cleanup(identity)
    receipt = emit_cleanup_receipt(
        identity, expected.cleanup, tmp_path, expected.written_at
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
        emit_cleanup_receipt(identity, expected.cleanup, tmp_path, ENDED)
    assert load_cleanup_receipt(identity, tmp_path) == expected
    assert [p.name for p in tmp_path.rglob("*") if p.is_file()] == ["cleanup.json"]


def test_failed_write_leaves_no_partial_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    identity = binding()

    def fail_sync(descriptor: int) -> None:
        raise OSError("disk failed")

    monkeypatch.setattr("syndicate.stock_receipt.os.fsync", fail_sync)
    with pytest.raises(OSError, match="disk failed"):
        emit_cleanup_receipt(identity, cleanup(identity).cleanup, tmp_path, ENDED)
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


@pytest.mark.parametrize("field", ["operation_id", "attempt_id", "run_id", "task_id"])
def test_rejects_cleanup_identity(field: str) -> None:
    identity = binding()
    receipt = cleanup(identity)
    invalid = receipt.model_copy(
        update={field: "different" if field == "task_id" else uuid4()}
    )
    with pytest.raises(ValueError, match="identity"):
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
    with pytest.raises(ValueError, match="Incomplete"):
        emit_cleanup_receipt(
            identity, CleanupReceipt(uid=10001, complete=False), tmp_path, ENDED
        )
    with pytest.raises(ValueError, match="timezone"):
        emit_cleanup_receipt(
            identity, cleanup(identity).cleanup, tmp_path, ENDED.replace(tzinfo=None)
        )
