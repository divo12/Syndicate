"""Controller-authorized Neatlogs reads; no payload files or fallback cache."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from syndicate.models.evidence import (
    Citation,
    CitationValidation,
    EvidenceGrant,
    EvidenceStatus,
    ManifestOverview,
    RecordCitation,
    RunEvidenceGrant,
    SearchPage,
    SpanContext,
    SpanExcerpt,
    SpanQuery,
    TextExcerpt,
    TraceCursor,
    TraceQuery,
)
from syndicate.observability.neatlogs_readback import (
    ExpectedTrace,
    NeatlogsReadbackReader,
    NeatlogsReadbackReceipt,
    ReadbackSpan,
)
from syndicate.services.benchmark import RunOutcome, RunReceipt, VerifierReceipt


class RunRecordView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: EvidenceStatus
    complete: bool
    receipt: RunReceipt | None = None


class VerifierResultView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: EvidenceStatus
    complete: bool
    receipt: VerifierReceipt | None = None


def matches(span: ReadbackSpan, query: TraceQuery) -> bool:
    fields = ((query.node_name, span.node_name), (query.node_type, span.node_type))
    if not all(expected is None or expected == actual for expected, actual in fields):
        return False
    return any(
        query.text in value
        for value in (span.node_name, span.input_text, span.output_text)
        if value is not None
    )


def excerpt(text: str | None, query: SpanQuery) -> TextExcerpt:
    end = query.offset + query.max_chars
    more = text is not None and len(text) > end
    return TextExcerpt(
        text=text[query.offset : end] if text is not None else None,
        next_offset=end if more else None,
        truncated=query.offset > 0 or more,
    )


def sufficient(receipt: NeatlogsReadbackReceipt) -> bool:
    return receipt.finalized and receipt.complete and bool(receipt.spans)


class EvidenceReader:
    def __init__(
        self,
        remote: NeatlogsReadbackReader,
        grants: tuple[EvidenceGrant, ...],
        run_grants: tuple[RunEvidenceGrant, ...] = (),
        run_receipts: tuple[RunReceipt, ...] = (),
    ) -> None:
        self.remote = remote
        self.grants = grants
        self.run_grants = run_grants
        self.run_receipts = run_receipts

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
            receipt = self.remote.fetch(ExpectedTrace(receipt=grant.receipt))
        except (OSError, ValueError):
            return EvidenceStatus.INCOMPLETE, None
        actual = (
            receipt.link,
            receipt.trace_ref,
            receipt.semantic_digest,
            receipt.binding_digest,
        )
        expected = (
            grant.link,
            grant.trace_ref,
            grant.semantic_digest,
            grant.receipt.binding_digest,
        )
        if actual != expected:
            return EvidenceStatus.MISALIGNED, None
        if not sufficient(receipt):
            return EvidenceStatus.INCOMPLETE, None
        return EvidenceStatus.RESOLVED, receipt

    def validate_citation(self, citation: Citation) -> CitationValidation:
        if isinstance(citation, RecordCitation):
            status, record_receipt = self._record_receipt(citation)
            return CitationValidation(
                status=status, complete=record_receipt is not None
            )
        status, receipt = self._read(citation.run_id, citation.trace_ref)
        if receipt is not None and citation.span_ref not in tuple(
            span.span_id for span in receipt.spans
        ):
            status = EvidenceStatus.MISSING
        return CitationValidation(
            status=status, complete=status == EvidenceStatus.RESOLVED
        )

    def read_run_record(self, citation: RecordCitation) -> RunRecordView:
        status, receipt = self._record_receipt(citation)
        return RunRecordView(
            status=status, complete=receipt is not None, receipt=receipt
        )

    def read_verifier_result(self, citation: RecordCitation) -> VerifierResultView:
        status, receipt = self._record_receipt(citation)
        verifier = receipt.verifier if receipt is not None else None
        return VerifierResultView(
            status=status, complete=verifier is not None, receipt=verifier
        )

    def _record_receipt(
        self, citation: RecordCitation
    ) -> tuple[EvidenceStatus, RunReceipt | None]:
        grant = self._record_grant(citation)
        if grant is None:
            return self._ungranted_record(citation), None
        receipt = self._receipt_for(grant)
        if receipt is None:
            return EvidenceStatus.MISSING, None
        if not _aligned_receipt(grant, receipt):
            return EvidenceStatus.MISALIGNED, None
        if not _admissible_receipt(receipt):
            return EvidenceStatus.INCOMPLETE, None
        return EvidenceStatus.RESOLVED, receipt

    def _record_grant(self, citation: RecordCitation) -> RunEvidenceGrant | None:
        return next(
            (
                item
                for item in self.run_grants
                if (item.run_id, item.record_ref)
                == (citation.run_id, citation.record_ref)
            ),
            None,
        )

    def _ungranted_record(self, citation: RecordCitation) -> EvidenceStatus:
        if any(item.run_id == citation.run_id for item in self.run_grants):
            return EvidenceStatus.MISALIGNED
        return EvidenceStatus.FORBIDDEN

    def _receipt_for(self, grant: RunEvidenceGrant) -> RunReceipt | None:
        return next(
            (item for item in self.run_receipts if item.run_id == grant.run_id), None
        )

    def search_trajectory(self, query: TraceQuery) -> SearchPage:
        status, receipt = self._read(query.run_id, query.trace_ref)
        if receipt is None:
            return SearchPage(status=status, complete=False)
        offset = 0
        if query.cursor:
            if (query.cursor.semantic_digest, query.cursor.query_hash) != (
                receipt.semantic_digest,
                query.content_hash,
            ):
                raise ValueError("Cursor does not match remote evidence and query")
            offset = query.cursor.offset
        # ponytail: scan in memory; add an index if measured latency requires it.
        refs = tuple(span.span_id for span in receipt.spans if matches(span, query))
        end = offset + query.limit
        has_more = end < len(refs)
        cursor = (
            TraceCursor(
                semantic_digest=receipt.semantic_digest,
                query_hash=query.content_hash,
                offset=end,
            )
            if has_more
            else None
        )
        return SearchPage(
            status=status,
            complete=True,
            span_refs=refs[offset:end],
            has_more=has_more,
            truncated=has_more,
            next_cursor=cursor,
        )

    def read_span_context(self, query: SpanQuery) -> SpanContext:
        status, receipt = self._read(query.run_id, query.trace_ref)
        if receipt is None:
            return SpanContext(status=status, complete=False)
        refs = tuple(span.span_id for span in receipt.spans)
        if query.span_ref not in refs:
            return SpanContext(status=EvidenceStatus.MISSING, complete=False)
        index = refs.index(query.span_ref)
        selected = receipt.spans[max(0, index - query.before) : index + query.after + 1]
        spans = tuple(
            SpanExcerpt(
                span_ref=span.span_id,
                input=excerpt(span.input_text, query),
                output=excerpt(span.output_text, query),
            )
            for span in selected
        )
        return SpanContext(status=status, complete=True, spans=spans)

    def get_trace_manifest(self, run_id: UUID, trace_ref: str) -> ManifestOverview:
        status, receipt = self._read(run_id, trace_ref)
        if receipt is None:
            return ManifestOverview(status=status, complete=False, trace_ref=trace_ref)
        return ManifestOverview(
            status=status,
            complete=True,
            link=receipt.link,
            trace_ref=receipt.trace_ref,
            semantic_digest=receipt.semantic_digest,
            span_count=len(receipt.spans),
        )


def _aligned_receipt(grant: RunEvidenceGrant, receipt: RunReceipt) -> bool:
    return (grant.operation_id, grant.attempt_id, grant.run_id, grant.task_id) == (
        receipt.operation_id,
        receipt.attempt_id,
        receipt.run_id,
        receipt.task_id,
    ) and grant.record_ref == receipt.verifier.raw_result_ref


def _admissible_receipt(receipt: RunReceipt) -> bool:
    return (
        receipt.cleanup_complete
        and receipt.outcome in (RunOutcome.PASS, RunOutcome.FAIL)
        and receipt.verifier.outcome is receipt.outcome
    )
