"""Controller-owned receipt bridge for Harbor's stock single-step lifecycle."""

from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from harbor.models.trial.result import AgentInfo, TimingInfo
from harbor.models.verifier.result import VerifierResult

from syndicate.benchmark import RunReceipt, classify_verifier
from syndicate.cli_envelope import WireModel
from syndicate.harbor_agent import CleanupReceipt

AGENT_NAME = "syndicate-nexau"


class ControllerTrialBinding(WireModel):
    operation_id: UUID
    attempt_id: UUID
    run_id: UUID
    task_id: str


class CleanupControlReceipt(ControllerTrialBinding):
    cleanup: CleanupReceipt
    written_at: datetime


class _StockResult(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def task_name(self) -> str: ...

    @property
    def agent_info(self) -> AgentInfo: ...

    @property
    def agent_execution(self) -> TimingInfo | None: ...

    @property
    def verifier(self) -> TimingInfo | None: ...

    @property
    def verifier_result(self) -> VerifierResult | None: ...

    @property
    def exception_info(self) -> object | None: ...


def _path(binding: ControllerTrialBinding, root: Path) -> Path:
    return root / str(binding.operation_id) / str(binding.attempt_id) / "cleanup.json"


def emit_cleanup_receipt(
    binding: ControllerTrialBinding,
    cleanup: CleanupReceipt,
    root: Path,
    written_at: datetime,
) -> CleanupControlReceipt:
    """Write the post-settlement proof once; this file never contains a trajectory."""
    if not cleanup.complete:
        raise ValueError("Incomplete cleanup cannot authorize a stock trial receipt")
    receipt = CleanupControlReceipt(
        operation_id=binding.operation_id,
        attempt_id=binding.attempt_id,
        run_id=binding.run_id,
        task_id=binding.task_id,
        cleanup=cleanup,
        written_at=written_at,
    )
    path = _path(binding, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(receipt.model_dump_json())
    return receipt


def load_cleanup_receipt(
    binding: ControllerTrialBinding, root: Path
) -> CleanupControlReceipt:
    receipt = CleanupControlReceipt.model_validate_json(
        _path(binding, root).read_bytes()
    )
    if not _matches(receipt, binding):
        raise ValueError("Cleanup receipt identity does not match controller binding")
    return receipt


def postprocess_stock_result(
    binding: ControllerTrialBinding,
    cleanup: CleanupControlReceipt,
    result: _StockResult,
    raw_result_ref: str,
) -> RunReceipt:
    """Correlate one stock Harbor result; never invoke a verifier."""
    if not _matches(cleanup, binding):
        raise ValueError("Cleanup receipt identity does not match controller binding")
    if result.id != binding.run_id:
        raise ValueError("Harbor run ID does not match controller binding")
    if result.task_name.rsplit("/", 1)[-1] != binding.task_id:
        raise ValueError("Harbor task does not match controller binding")
    if result.agent_info.name != AGENT_NAME:
        raise ValueError("Harbor agent identity does not match Syndicate adapter")
    if result.exception_info is not None or result.verifier_result is None:
        raise ValueError("Stock Harbor result is incomplete")
    timing = _timing(result.agent_execution, result.verifier)
    verifier = classify_verifier(result.verifier_result, raw_result_ref)
    return RunReceipt(
        operation_id=binding.operation_id,
        attempt_id=binding.attempt_id,
        run_id=binding.run_id,
        task_id=binding.task_id,
        cleanup_complete=cleanup.cleanup.complete,
        cleanup=cleanup.cleanup,
        outcome=verifier.outcome,
        verifier=verifier,
        agent_finished_at=timing[0],
        verifier_started_at=timing[1],
    )


def _timing(
    agent: TimingInfo | None, verifier: TimingInfo | None
) -> tuple[datetime, datetime]:
    if (
        agent is None
        or verifier is None
        or agent.finished_at is None
        or verifier.started_at is None
        or agent.finished_at >= verifier.started_at
    ):
        raise ValueError("Stock verifier must begin after agent cleanup returns")
    return agent.finished_at, verifier.started_at


def _matches(receipt: CleanupControlReceipt, binding: ControllerTrialBinding) -> bool:
    return (
        receipt.operation_id == binding.operation_id
        and receipt.attempt_id == binding.attempt_id
        and receipt.run_id == binding.run_id
        and receipt.task_id == binding.task_id
    )
