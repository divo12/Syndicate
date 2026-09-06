"""Controller-owned Harbor cleanup proofs; never a public agent-facing writer."""

import hmac
import os
import secrets
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

from harbor.models.trial.result import TimingInfo, TrialResult
from pydantic import AwareDatetime, Field

from syndicate.adapters.harbor_agent import CleanupReceipt
from syndicate.models.envelope import WireModel
from syndicate.services.benchmark import RunReceipt, classify_verifier

AGENT_NAME = "syndicate-nexau"
AGENT_IMPORT = "syndicate.adapters.harbor_adapter.SyndicateNexAUAgent"
CONTROLLER_UID = 10001
_CONTROLLER_SEAL_KEY = secrets.token_bytes(32)


class ControllerTrialBinding(WireModel):
    operation_id: UUID
    attempt_id: UUID
    run_id: UUID
    task_id: str
    environment_context_id: str = Field(min_length=1, default="controller")


class _ControllerAuthority(WireModel):
    binding: ControllerTrialBinding
    root: Path


class CleanupControlReceipt(ControllerTrialBinding):
    cleanup: CleanupReceipt
    agent_import: str
    agent_name: str
    uid: int
    written_at: AwareDatetime
    controller_seal: str = ""


def _controller_authority(
    binding: ControllerTrialBinding, controller_root: Path
) -> _ControllerAuthority:
    return _ControllerAuthority(binding=binding, root=controller_root)


def _write_settled_cleanup(
    authority: _ControllerAuthority, cleanup: CleanupReceipt, written_at: datetime
) -> CleanupControlReceipt:
    """Persist one settled controller cleanup proof after HarborAgent.run returned."""
    if not cleanup.complete or cleanup.uid != CONTROLLER_UID:
        raise ValueError(
            "Only settled controller cleanup may authorize a stock receipt"
        )
    provisional = CleanupControlReceipt(
        **authority.binding.model_dump(),
        cleanup=cleanup,
        agent_import=AGENT_IMPORT,
        agent_name=AGENT_NAME,
        uid=cleanup.uid,
        written_at=written_at,
    )
    receipt = provisional.model_copy(
        update={"controller_seal": _cleanup_seal(provisional)}
    )
    parent = _receipt_parent(authority.binding, authority.root)
    temporary = ".cleanup-" + uuid4().hex
    temporary_fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
        dir_fd=parent,
    )
    try:
        with os.fdopen(temporary_fd, "w", encoding="utf-8") as output:
            output.write(receipt.model_dump_json())
            output.flush()
            os.fsync(output.fileno())
        os.link(
            temporary,
            "cleanup.json",
            src_dir_fd=parent,
            dst_dir_fd=parent,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=parent)
        os.fsync(parent)
    except BaseException:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(parent)
    return receipt


def load_cleanup_receipt(
    binding: ControllerTrialBinding, root: Path
) -> CleanupControlReceipt:
    parent = _receipt_parent(binding, root)
    try:
        descriptor = os.open("cleanup.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as source:
            receipt = CleanupControlReceipt.model_validate_json(source.read())
    finally:
        os.close(parent)
    if not _matches(receipt, binding) or not _authentic(receipt):
        raise ValueError("Cleanup receipt is not controller-authentic")
    return receipt


def postprocess_stock_result(
    binding: ControllerTrialBinding,
    cleanup: CleanupControlReceipt,
    result: TrialResult,
    raw_result_ref: str,
) -> RunReceipt:
    """Correlate one stock Harbor result; never invoke a verifier."""
    if not _matches(cleanup, binding) or not _authentic(cleanup):
        raise ValueError("Cleanup receipt is not controller-authentic")
    if result.id != binding.run_id:
        raise ValueError("Harbor run ID does not match controller binding")
    if result.task_name.rsplit("/", 1)[-1] != binding.task_id:
        raise ValueError("Harbor task does not match controller binding")
    if result.agent_info.name != AGENT_NAME:
        raise ValueError("Harbor agent identity does not match Syndicate adapter")
    if result.exception_info is not None or result.verifier_result is None:
        raise ValueError("Stock Harbor result is incomplete")
    agent_finished_at, verifier_started_at = _timing(
        result.agent_execution, result.verifier, cleanup.written_at
    )
    verifier = classify_verifier(result.verifier_result, raw_result_ref)
    return RunReceipt(
        operation_id=binding.operation_id,
        attempt_id=binding.attempt_id,
        run_id=binding.run_id,
        task_id=binding.task_id,
        cleanup=cleanup.cleanup,
        outcome=verifier.outcome,
        verifier=verifier,
        agent_finished_at=agent_finished_at,
        verifier_started_at=verifier_started_at,
    )


def _receipt_parent(binding: ControllerTrialBinding, root: Path) -> int:
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        current = root_fd
        for name in (str(binding.operation_id), str(binding.attempt_id)):
            try:
                os.mkdir(name, mode=0o700, dir_fd=current)
            except FileExistsError:
                pass
            child = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current,
            )
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(root_fd)
        raise


def _authentic(receipt: CleanupControlReceipt) -> bool:
    return (
        receipt.cleanup.complete
        and receipt.cleanup.uid == CONTROLLER_UID
        and receipt.uid == CONTROLLER_UID
        and receipt.agent_import == AGENT_IMPORT
        and receipt.agent_name == AGENT_NAME
        and hmac.compare_digest(receipt.controller_seal, _cleanup_seal(receipt))
    )


def _cleanup_seal(receipt: CleanupControlReceipt) -> str:
    content = receipt.model_dump_json(exclude={"controller_seal"}).encode()
    return hmac.digest(_CONTROLLER_SEAL_KEY, content, "sha256").hex()


def _timing(
    agent: TimingInfo | None, verifier: TimingInfo | None, written_at: datetime
) -> tuple[datetime, datetime]:
    agent_start, agent_end = _completed_interval(agent)
    verifier_start, _ = _completed_interval(verifier)
    if not agent_start <= written_at <= agent_end < verifier_start:
        raise ValueError("Stock verifier must begin after agent cleanup returns")
    return agent_end, verifier_start


def _completed_interval(timing: TimingInfo | None) -> tuple[datetime, datetime]:
    if timing is None or timing.started_at is None or timing.finished_at is None:
        raise ValueError("Stock execution timing is incomplete")
    start, end = timing.started_at, timing.finished_at
    if start.utcoffset() is None or end.utcoffset() is None or start > end:
        raise ValueError("Stock execution timing must be aware and ordered")
    return start, end


def _matches(receipt: CleanupControlReceipt, binding: ControllerTrialBinding) -> bool:
    return (
        receipt.operation_id == binding.operation_id
        and receipt.attempt_id == binding.attempt_id
        and receipt.run_id == binding.run_id
        and receipt.task_id == binding.task_id
        and receipt.environment_context_id == binding.environment_context_id
    )
