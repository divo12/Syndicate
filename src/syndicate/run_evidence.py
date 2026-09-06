"""Controller-granted, run-aligned evidence over trusted verifier receipts."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from syndicate.benchmark import RunOutcome, RunReceipt, VerifierReceipt
from syndicate.evidence_contracts import EvidenceStatus, RecordCitation


class RunEvidenceObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class RunEvidenceKind(StrEnum):
    RUN_RECORD = "run_record"
    VERIFIER_RESULT = "verifier_result"


class RunEvidenceQuery(RunEvidenceObject):
    operation_id: UUID
    attempt_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1)
    kind: RunEvidenceKind


class RunRecordEvidence(RunEvidenceObject):
    status: EvidenceStatus
    complete: bool
    citation: RecordCitation | None = None
    receipt: RunReceipt | None = None


class VerifierEvidence(RunEvidenceObject):
    status: EvidenceStatus
    complete: bool
    citation: RecordCitation | None = None
    receipt: VerifierReceipt | None = None


class RunEvidenceReader:
    """Read only receipts that the controller granted to this investigation."""

    def __init__(self, receipts: tuple[RunReceipt, ...]) -> None:
        keys = tuple(_key(receipt) for receipt in receipts)
        if len(keys) != len(set(keys)):
            raise ValueError("Duplicate controller-granted run receipt")
        self._receipts = receipts

    def read_run_record(self, query: RunEvidenceQuery) -> RunRecordEvidence:
        status, receipt = self._resolve(query, RunEvidenceKind.RUN_RECORD)
        if receipt is None:
            return RunRecordEvidence(status=status, complete=False)
        return RunRecordEvidence(
            status=status,
            complete=True,
            citation=RecordCitation(
                run_id=receipt.run_id,
                record_ref=_run_record_ref(receipt),
            ),
            receipt=receipt,
        )

    def read_verifier_result(self, query: RunEvidenceQuery) -> VerifierEvidence:
        status, receipt = self._resolve(query, RunEvidenceKind.VERIFIER_RESULT)
        if receipt is None:
            return VerifierEvidence(status=status, complete=False)
        return VerifierEvidence(
            status=status,
            complete=True,
            citation=RecordCitation(
                run_id=receipt.run_id,
                record_ref=receipt.verifier.raw_result_ref,
            ),
            receipt=receipt.verifier,
        )

    def _resolve(
        self, query: RunEvidenceQuery, expected: RunEvidenceKind
    ) -> tuple[EvidenceStatus, RunReceipt | None]:
        if query.kind is not expected:
            return EvidenceStatus.FORBIDDEN, None
        receipt = next(
            (item for item in self._receipts if _key(item) == _key(query)), None
        )
        if receipt is None:
            status = (
                EvidenceStatus.FORBIDDEN
                if any(item.run_id == query.run_id for item in self._receipts)
                else EvidenceStatus.MISSING
            )
            return status, None
        if not receipt.cleanup_complete or receipt.outcome is RunOutcome.UNVERIFIED:
            return EvidenceStatus.INCOMPLETE, None
        return EvidenceStatus.RESOLVED, receipt


def _key(value: RunReceipt | RunEvidenceQuery) -> tuple[UUID, UUID, UUID, str]:
    return (value.operation_id, value.attempt_id, value.run_id, value.task_id)


def _run_record_ref(receipt: RunReceipt) -> str:
    return f"run:{receipt.operation_id}:{receipt.attempt_id}"
