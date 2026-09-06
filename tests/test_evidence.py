"""Neatlogs-only citation identities, distinct from controller run UUIDs."""

from uuid import UUID

import pytest
from pydantic import SecretStr, TypeAdapter, ValidationError

from syndicate.evidence import EvidenceReader
from syndicate.evidence_contracts import (
    Citation,
    EvidenceGrant,
    EvidenceStatus,
    RecordCitation,
    SpanCitation,
)
from syndicate.observability.neatlogs_capture import RunLink
from syndicate.observability.neatlogs_readback import (
    ExpectedTrace,
    NeatlogsReadbackReader,
    NeatlogsReadbackReceipt,
    ReadbackSpan,
)


def test_span_citation_preserves_remote_ids_without_uuid_coercion() -> None:
    citation = SpanCitation(
        run_id=UUID(int=1),
        trace_ref="a" * 32,
        span_ref="b" * 16,
    )
    assert citation.trace_ref == "a" * 32
    assert citation.span_ref == "b" * 16
    assert citation.run_id == UUID(int=1)


@pytest.mark.parametrize("span_ref", ["b" * 32, "B" * 16, "../trace", ""])
def test_span_citation_rejects_invalid_provider_ids(span_ref: str) -> None:
    with pytest.raises(ValidationError):
        SpanCitation(run_id=UUID(int=1), trace_ref="a" * 32, span_ref=span_ref)


def test_citation_variants_cannot_mix_span_and_record_fields() -> None:
    citation = RecordCitation(run_id=UUID(int=1), record_ref="record:opaque")
    restored: Citation = TypeAdapter(Citation).validate_json(citation.model_dump_json())
    assert restored == citation
    assert (
        setup_reader()[1].validate_citation(citation).status == EvidenceStatus.FORBIDDEN
    )
    mixed = citation.model_dump_json().replace('"record_ref"', '"span_ref"')
    with pytest.raises(ValidationError):
        TypeAdapter(Citation).validate_json(mixed)


class Remote(NeatlogsReadbackReader):
    """Synthetic in-memory service response; never a production fallback."""

    def __init__(self, response: NeatlogsReadbackReceipt) -> None:
        super().__init__(SecretStr("synthetic"))
        self.response = response
        self.unavailable = False

    def fetch(self, expected: ExpectedTrace) -> NeatlogsReadbackReceipt:
        if self.unavailable:
            raise ValueError("Remote unavailable")
        return self.response


def setup_reader(count: int = 1) -> tuple[Remote, EvidenceReader, SpanCitation]:
    link = RunLink(
        operation_id=UUID(int=2),
        attempt_id=UUID(int=3),
        run_id=UUID(int=1),
        task_id="task-a-1",
    )
    receipt = NeatlogsReadbackReceipt(
        link=link,
        trace_ref="a" * 32,
        finalized=True,
        complete=True,
        semantic_digest="sha256:" + "0" * 64,
        spans=tuple(
            ReadbackSpan(
                span_id=f"{index + 1:016x}",
                node_name="tool",
                node_type="tool_call",
                input_text="needle" if index == 0 else "later",
                output_text=str(index),
            )
            for index in range(count)
        ),
    )
    remote = Remote(receipt)
    reader = EvidenceReader(
        remote,
        (
            EvidenceGrant(
                link=link,
                trace_ref=receipt.trace_ref,
                expected_span_refs=tuple(span.span_id for span in receipt.spans),
                semantic_digest=receipt.semantic_digest,
            ),
        ),
    )
    return (
        remote,
        reader,
        SpanCitation(
            run_id=link.run_id,
            trace_ref=receipt.trace_ref,
            span_ref=receipt.spans[0].span_id,
        ),
    )


def test_citation_requires_authorized_persisted_complete_remote_evidence() -> None:
    remote, reader, citation = setup_reader()
    assert reader.validate_citation(citation).status == EvidenceStatus.RESOLVED
    remote.response = remote.response.model_copy(update={"finalized": False})
    assert reader.validate_citation(citation).status == EvidenceStatus.INCOMPLETE


def test_search_finds_early_remote_span_and_pages_in_order() -> None:
    from syndicate.evidence_contracts import TraceQuery

    _, reader, citation = setup_reader(120)
    query = TraceQuery(
        run_id=citation.run_id, trace_ref=citation.trace_ref, text="needle"
    )
    assert reader.search_trajectory(query).span_refs == (citation.span_ref,)
    query = query.model_copy(update={"text": "", "limit": 100})
    first = reader.search_trajectory(query)
    last = reader.search_trajectory(
        query.model_copy(update={"cursor": first.next_cursor})
    )
    assert len(first.span_refs) == 100 and first.has_more
    assert len(last.span_refs) == 20 and not last.has_more


def test_remote_context_is_bounded_and_tail_is_readable() -> None:
    from syndicate.evidence_contracts import SpanQuery

    _, reader, citation = setup_reader(3)
    query = SpanQuery(
        run_id=citation.run_id,
        trace_ref=citation.trace_ref,
        span_ref=citation.span_ref,
        max_chars=3,
    )
    first = reader.read_span_context(query)
    assert first.spans[0].input.text == "nee"
    assert first.spans[0].input.next_offset == 3
    tail = reader.read_span_context(query.model_copy(update={"offset": 3}))
    assert tail.spans[0].input.text == "dle"
    assert tail.spans[0].input.next_offset is None


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("missing", EvidenceStatus.MISSING),
        ("forbidden", EvidenceStatus.FORBIDDEN),
        ("link", EvidenceStatus.MISALIGNED),
        ("offline", EvidenceStatus.INCOMPLETE),
        ("payload", EvidenceStatus.INCOMPLETE),
    ],
)
def test_remote_failure_never_resolves_citation(
    fault: str, expected: EvidenceStatus
) -> None:
    remote, reader, citation = setup_reader()
    if fault == "missing":
        citation = citation.model_copy(update={"span_ref": "f" * 16})
    elif fault == "forbidden":
        citation = citation.model_copy(update={"run_id": UUID(int=99)})
    elif fault == "link":
        changed = remote.response.link.model_copy(update={"attempt_id": UUID(int=99)})
        remote.response = remote.response.model_copy(update={"link": changed})
    elif fault == "offline":
        remote.unavailable = True
    else:
        span = remote.response.spans[0].model_copy(update={"input_text": None})
        remote.response = remote.response.model_copy(update={"spans": (span,)})
    validation = reader.validate_citation(citation)
    assert validation.status == expected
    assert not validation.complete


def test_manifest_returns_control_metadata_only_and_changed_cursor_fails() -> None:
    from syndicate.evidence_contracts import TraceQuery

    _, reader, citation = setup_reader(2)
    overview = reader.get_trace_manifest(citation.run_id, citation.trace_ref)
    assert overview.complete and overview.span_count == 2
    assert "needle" not in overview.model_dump_json()
    query = TraceQuery(run_id=citation.run_id, trace_ref=citation.trace_ref, limit=1)
    page = reader.search_trajectory(query)
    with pytest.raises(ValueError, match="Cursor"):
        reader.search_trajectory(
            query.model_copy(update={"text": "changed", "cursor": page.next_cursor})
        )
