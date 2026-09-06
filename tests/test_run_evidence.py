from uuid import UUID

from syndicate.benchmark import (
    RunOutcome,
    RunReceipt,
    VerifierReason,
    VerifierReceipt,
)
from syndicate.evidence_contracts import EvidenceStatus
from syndicate.run_evidence import RunEvidenceKind, RunEvidenceQuery, RunEvidenceReader


def receipt(
    cleanup_complete: bool = True,
    outcome: RunOutcome = RunOutcome.FAIL,
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
        cleanup_complete=cleanup_complete,
        outcome=outcome,
        verifier=verifier,
    )


def query(kind: RunEvidenceKind) -> RunEvidenceQuery:
    return RunEvidenceQuery(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        run_id=UUID(int=3),
        task_id="task-a-1",
        kind=kind,
    )


def test_run_and_verifier_evidence_share_controller_granted_receipt() -> None:
    reader = RunEvidenceReader((receipt(),))
    run = reader.read_run_record(query(RunEvidenceKind.RUN_RECORD))
    verifier = reader.read_verifier_result(query(RunEvidenceKind.VERIFIER_RESULT))
    assert run.status is EvidenceStatus.RESOLVED
    assert run.receipt == receipt()
    assert verifier.status is EvidenceStatus.RESOLVED
    assert verifier.receipt == receipt().verifier
    assert verifier.citation is not None
    assert verifier.citation.run_id == receipt().run_id
    assert verifier.citation.record_ref == "harbor:opaque"


def test_wrong_controller_correlation_is_forbidden() -> None:
    reader = RunEvidenceReader((receipt(),))
    wrong = query(RunEvidenceKind.VERIFIER_RESULT).model_copy(
        update={"attempt_id": UUID(int=99)}
    )
    result = reader.read_verifier_result(wrong)
    assert result.status is EvidenceStatus.FORBIDDEN
    assert not result.complete


def test_incomplete_cleanup_blocks_all_run_aligned_evidence() -> None:
    reader = RunEvidenceReader(
        (receipt(cleanup_complete=False, outcome=RunOutcome.UNVERIFIED),)
    )
    assert (
        reader.read_run_record(query(RunEvidenceKind.RUN_RECORD)).status
        is EvidenceStatus.INCOMPLETE
    )
    assert (
        reader.read_verifier_result(query(RunEvidenceKind.VERIFIER_RESULT)).status
        is EvidenceStatus.INCOMPLETE
    )


def test_unverified_outcome_and_wrong_kind_never_resolve() -> None:
    reader = RunEvidenceReader((receipt(outcome=RunOutcome.UNVERIFIED),))
    assert (
        reader.read_verifier_result(query(RunEvidenceKind.VERIFIER_RESULT)).status
        is EvidenceStatus.INCOMPLETE
    )
    assert (
        reader.read_verifier_result(query(RunEvidenceKind.RUN_RECORD)).status
        is EvidenceStatus.FORBIDDEN
    )
