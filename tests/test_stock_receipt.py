from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from harbor.models.trial.result import AgentInfo, TimingInfo
from harbor.models.verifier.result import VerifierResult

from syndicate.benchmark import RunOutcome
from syndicate.harbor_agent import CleanupReceipt
from syndicate.stock_receipt import (
    ControllerTrialBinding,
    emit_cleanup_receipt,
    load_cleanup_receipt,
    postprocess_stock_result,
)


@dataclass(frozen=True)
class StockResult:
    id: UUID
    task_name: str
    agent_info: AgentInfo
    agent_execution: TimingInfo
    verifier: TimingInfo
    verifier_result: VerifierResult | None
    exception_info: object | None = None


def binding() -> ControllerTrialBinding:
    return ControllerTrialBinding(
        operation_id=uuid4(), attempt_id=uuid4(), run_id=uuid4(), task_id="task-a-1"
    )


def result(identity: ControllerTrialBinding) -> StockResult:
    ended = datetime(2026, 9, 6, 7, 0, tzinfo=UTC)
    return StockResult(
        id=identity.run_id,
        task_name="owner/task-a-1",
        agent_info=AgentInfo(name="syndicate-nexau", version="0.1.0"),
        agent_execution=TimingInfo(finished_at=ended),
        verifier=TimingInfo(started_at=ended + timedelta(seconds=1)),
        verifier_result=VerifierResult(rewards={"reward": 0}),
    )


def test_controller_receipt_is_atomic_and_postprocesses_stock_verifier(
    tmp_path: Path,
) -> None:
    identity = binding()
    receipt = emit_cleanup_receipt(
        identity,
        CleanupReceipt(uid=10001, complete=True),
        tmp_path,
        datetime(2026, 9, 6, tzinfo=UTC),
    )
    loaded = load_cleanup_receipt(identity, tmp_path)
    terminal = postprocess_stock_result(
        identity, loaded, result(identity), "harbor:run"
    )

    assert receipt == loaded
    assert terminal.outcome is RunOutcome.FAIL
    assert terminal.verifier.reward == 0.0


def test_postprocessor_rejects_mismatched_identity_or_order(tmp_path: Path) -> None:
    identity = binding()
    receipt = emit_cleanup_receipt(
        identity,
        CleanupReceipt(uid=10001, complete=True),
        tmp_path,
        datetime(2026, 9, 6, tzinfo=UTC),
    )
    invalid = replace(result(identity), id=uuid4())

    with pytest.raises(ValueError, match="run"):
        postprocess_stock_result(identity, receipt, invalid, "harbor:run")


def test_postprocessor_rejects_missing_stock_verifier_result(tmp_path: Path) -> None:
    identity = binding()
    receipt = emit_cleanup_receipt(
        identity,
        CleanupReceipt(uid=10001, complete=True),
        tmp_path,
        datetime(2026, 9, 6, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="incomplete"):
        postprocess_stock_result(
            identity,
            receipt,
            replace(result(identity), verifier_result=None),
            "harbor:run",
        )
