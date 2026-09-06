"""MCP readback contracts; fake responses never write trajectory payloads."""

from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from syndicate.observability.neatlogs_capture import RunLink
from syndicate.observability.neatlogs_readback import (
    ExpectedTrace,
    NeatlogsReadbackReader,
    NeatlogsTraceRef,
)


class Reader(NeatlogsReadbackReader):
    def __init__(self, search: str, context: str) -> None:
        super().__init__(SecretStr("test"))
        self.search = search
        self.context = context

    def _tool(self, name: str, arguments: str) -> str:
        return self.search if name == "search_traces" else self.context


def expected() -> ExpectedTrace:
    link = RunLink(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        run_id=UUID(int=3),
        task_id="task",
    )
    return ExpectedTrace(
        link=link, trace_ref="trace", expected_span_refs=("root", "child")
    )


def context(link: RunLink, status: str = "success", truncated: bool = False) -> str:
    return (
        '{"trace_id":"trace","status":"'
        + status
        + '","span_count":2,"truncated":'
        + str(truncated).lower()
        + ',"root_span":{"span_id":"root","span_type":"WORKFLOW","name":"run",'
        + '"status":"success","input":"in","output":"out","metadata":'
        + link.model_dump_json()
        + ',"children":[{"span_id":"child","span_type":"TOOL","name":"tool",'
        + '"status":"success","input":"in","output":"out"}]}}'
    )


def reader(value: ExpectedTrace, raw: str) -> Reader:
    return Reader('{"traces":[{"trace_id":"trace"}]}', raw)


def test_complete_readback_requires_link_coverage_and_finalization() -> None:
    value = expected()
    receipt = reader(value, context(value.link)).fetch(value)
    assert receipt.finalized and receipt.complete
    assert tuple(span.span_id for span in receipt.spans) == ("root", "child")


@pytest.mark.parametrize(
    ("search", "raw"),
    [
        ('{"traces":[]}', None),
        (
            '{"traces":[{"trace_id":"trace"}]}',
            '{"trace_id":"trace","status":"error","span_count":1,"root_span":{"span_id":"root","span_type":"WORKFLOW","name":"run","status":"error"}}',
        ),
        ('{"traces":[{"trace_id":"trace"}]}', None),
    ],
)
def test_missing_or_unfinalized_readback_is_incomplete(
    search: str, raw: str | None
) -> None:
    value = expected()
    response = raw if raw is not None else context(value.link, truncated=True)
    receipt = Reader(search, response).fetch(value)
    assert not receipt.complete


def test_mismatch_and_truncation_are_incomplete() -> None:
    value = expected()
    wrong = value.link.model_copy(update={"attempt_id": UUID(int=9)})
    assert not reader(value, context(wrong)).fetch(value).complete
    assert not reader(value, context(value.link, truncated=True)).fetch(value).complete


def test_legacy_read_cannot_bypass_expected_span_set() -> None:
    with pytest.raises(ValueError, match="coverage"):
        NeatlogsReadbackReader(SecretStr("test")).read(
            expected().link, NeatlogsTraceRef("trace")
        )


def test_mcp_event_stream_envelope_is_supported() -> None:
    reader = NeatlogsReadbackReader(SecretStr("test"))
    assert reader._json_response('event: message\ndata: {"jsonrpc":"2.0"}\n') == (
        '{"jsonrpc":"2.0"}'
    )


def test_provider_result_aliases_remain_fail_closed_when_missing() -> None:
    value = expected()
    receipt = Reader('{"results":[]}', context(value.link)).fetch(value)
    assert not receipt.complete


def test_duplicate_trace_or_span_data_is_incomplete() -> None:
    value = expected()
    duplicate_trace = Reader(
        '{"traces":[{"trace_id":"trace"},{"trace_id":"trace"}]}',
        context(value.link),
    )
    assert not duplicate_trace.fetch(value).complete
    duplicate_span = (
        context(value.link)
        .replace(
            "}]}}",
            '},{"span_id":"child","span_type":"TOOL","name":"again","status":"success"}]}}',
        )
        .replace('"span_count":2', '"span_count":3')
    )
    assert not reader(value, duplicate_span).fetch(value).complete


def test_expected_span_ids_cannot_repeat() -> None:
    value = expected()
    with pytest.raises(ValidationError):
        ExpectedTrace(
            link=value.link,
            trace_ref=value.trace_ref,
            expected_span_refs=("root", "root"),
        )
