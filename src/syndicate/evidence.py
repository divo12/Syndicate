"""Controller-authorized Neatlogs reads; no payload files or fallback cache."""

from uuid import UUID

from syndicate.evidence_contracts import (
    Citation,
    CitationValidation,
    EvidenceGrant,
    EvidenceStatus,
    RecordCitation,
)
from syndicate.observability.neatlogs_readback import (
    NeatlogsReadbackReader,
    NeatlogsReadbackReceipt,
    NeatlogsTraceRef,
)


class EvidenceReader:
    def __init__(
        self, remote: NeatlogsReadbackReader, grants: tuple[EvidenceGrant, ...]
    ) -> None:
        self.remote = remote
        self.grants = grants

    def _read(
        self, run_id: UUID, trace_ref: str
    ) -> tuple[EvidenceStatus, NeatlogsReadbackReceipt | None]:
        grant = next(
            (
                item
                for item in self.grants
                if (item.link.run_id, item.trace_ref) == (run_id, trace_ref)
            ),
            None,
        )
        if grant is None:
            return EvidenceStatus.FORBIDDEN, None
        try:
            receipt = self.remote.read(grant.link, NeatlogsTraceRef(trace_ref))
        except (OSError, ValueError):
            return EvidenceStatus.INCOMPLETE, None
        if (receipt.link, receipt.trace_ref, receipt.semantic_digest) != (
            grant.link,
            grant.trace_ref,
            grant.semantic_digest,
        ):
            return EvidenceStatus.MISALIGNED, None
        if not receipt.finalized or not receipt.complete or not receipt.spans:
            return EvidenceStatus.INCOMPLETE, None
        return EvidenceStatus.RESOLVED, receipt

    def validate_citation(self, citation: Citation) -> CitationValidation:
        if isinstance(citation, RecordCitation):
            return CitationValidation(status=EvidenceStatus.INCOMPLETE, complete=False)
        status, receipt = self._read(citation.run_id, citation.trace_ref)
        if receipt is not None and citation.span_ref not in tuple(
            span.span_id for span in receipt.spans
        ):
            status = EvidenceStatus.MISSING
        return CitationValidation(
            status=status, complete=status == EvidenceStatus.RESOLVED
        )
