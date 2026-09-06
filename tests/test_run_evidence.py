from uuid import UUID

from test_task_judge import GRANT, Remote

from syndicate.adapters.harbor_agent import CleanupReceipt
from syndicate.models.evidence import EvidenceStatus, RecordCitation, RunEvidenceGrant
from syndicate.services.benchmark import (
    RunOutcome,
    RunReceipt,
    VerifierReason,
    VerifierReceipt,
)
from syndicate.services.evidence import EvidenceReader


def receipt(
    cleanup_complete: bool = True, outcome: RunOutcome = RunOutcome.FAIL
) -> RunReceipt:
    verifier = VerifierReceipt(
        outcome=outcome,
        reason=(
            VerifierReason.FAILED
            if outcome is RunOutcome.FAIL
            else VerifierReason.MISSING_RESULT
        ),
        reward=0.0 if outcome is RunOutcome.FAIL else None,
        raw_result_ref="harbor:opaque",
    )
    return RunReceipt(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        run_id=UUID(int=3),
        task_id="task-a-1",
        cleanup=CleanupReceipt(complete=cleanup_complete),
        outcome=outcome,
        verifier=verifier,
    )


def grant() -> RunEvidenceGrant:
    return RunEvidenceGrant(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        run_id=UUID(int=3),
        task_id="task-a-1",
        record_ref="harbor:opaque",
    )


def citation() -> RecordCitation:
    return RecordCitation(run_id=UUID(int=3), record_ref="harbor:opaque")


def reader(receipts: tuple[RunReceipt, ...] = (receipt(),)) -> EvidenceReader:
    return EvidenceReader(Remote(), (GRANT,), (grant(),), receipts)


def test_granted_record_resolves_and_returns_nonpayload_views() -> None:
    value = reader()
    assert value.validate_citation(citation()).status is EvidenceStatus.RESOLVED
    assert value.read_run_record(citation()).receipt == receipt()
    assert value.read_verifier_result(citation()).receipt == receipt().verifier


def test_missing_grant_or_receipt_never_resolves() -> None:
    missing = reader(())
    assert missing.validate_citation(citation()).status is EvidenceStatus.MISSING
    ungranted = RecordCitation(run_id=UUID(int=99), record_ref="harbor:opaque")
    assert reader().validate_citation(ungranted).status is EvidenceStatus.FORBIDDEN


def test_identity_or_reference_mismatch_is_misaligned() -> None:
    wrong_ref = RecordCitation(run_id=UUID(int=3), record_ref="harbor:other")
    assert reader().validate_citation(wrong_ref).status is EvidenceStatus.MISALIGNED
    changed = receipt().model_copy(update={"attempt_id": UUID(int=99)})
    assert (
        reader((changed,)).validate_citation(citation()).status
        is EvidenceStatus.MISALIGNED
    )


def test_incomplete_cleanup_or_unverified_outcome_blocks_record() -> None:
    unverified = receipt(cleanup_complete=False, outcome=RunOutcome.UNVERIFIED)
    result = reader((unverified,)).validate_citation(citation())
    assert result.status is EvidenceStatus.INCOMPLETE
    assert not result.complete
