"""Neatlogs-only citation identities, distinct from controller run UUIDs."""

from uuid import UUID

import pytest
from pydantic import TypeAdapter, ValidationError

from syndicate.evidence_contracts import Citation, RecordCitation, SpanCitation


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
    restored = TypeAdapter(Citation).validate_json(citation.model_dump_json())
    assert restored == citation
    mixed = citation.model_dump_json().replace('"record_ref"', '"span_ref"')
    with pytest.raises(ValidationError):
        TypeAdapter(Citation).validate_json(mixed)
