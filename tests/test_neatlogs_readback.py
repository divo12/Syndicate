"""MCP v2 finalization and linkage contracts."""

from collections.abc import Callable
from uuid import UUID

import pytest
from pydantic import SecretStr, ValidationError

from syndicate.observability.neatlogs_capture import RunLink
from syndicate.observability.neatlogs_readback import (
    ExpectedTrace,
    NeatlogsReadbackReader,
)


class Reader(NeatlogsReadbackReader):
    def __init__(self, response: str) -> None:
        super().__init__(SecretStr("test"))
        self.response = response

    def _tool(self, name: str, arguments: str) -> str:
        return self.response


def expected() -> ExpectedTrace:
    return ExpectedTrace(
        link=RunLink(
            operation_id=UUID(int=1),
            attempt_id=UUID(int=2),
            run_id=UUID(int=3),
            task_id="task",
        ),
        trace_ref="trace",
        expected_span_refs=("root", "child"),
    )


def context(
    link: RunLink | None,
    *,
    finalization: str = "finalized",
    ready: bool = True,
    payload: bool = True,
    tree: bool = True,
    truncated: bool = False,
    count: int = 2,
) -> str:
    metadata = link.model_dump_json() if link is not None else "null"
    return (
        '{"trace_id":"trace","status":"success","finalization_status":"'
        + finalization
        + '","verification_ready":'
        + str(ready).lower()
        + ',"span_payload_complete":'
        + str(payload).lower()
        + ',"span_tree_complete":'
        + str(tree).lower()
        + ',"span_count":'
        + str(count)
        + ',"returned_span_count":'
        + str(count)
        + ',"root_span_count":1,"truncated":'
        + str(truncated).lower()
        + ',"spans":[{"span_id":"root","name":"run","type":"WORKFLOW","metadata":'
        + metadata
        + '},{"span_id":"child","parent_span_id":"root","name":"tool","type":"TOOL"}]}'
    )


def test_known_finalized_v2_context_is_complete() -> None:
    value = expected()
    receipt = Reader(context(value.link)).fetch(value)
    assert receipt.finalized and receipt.complete


@pytest.mark.parametrize(
    "raw",
    [
        lambda link: context(link, finalization="pending"),
        lambda link: context(link, ready=False),
        lambda link: context(link, payload=False),
        lambda link: context(link, tree=False),
        lambda link: context(link, truncated=True),
        lambda link: context(link, count=1),
        lambda link: context(None),
    ],
)
def test_finality_and_linkage_gaps_are_incomplete(
    raw: Callable[[RunLink], str],
) -> None:
    value = expected()
    receipt = Reader(raw(value.link)).fetch(value)
    assert not receipt.complete


def test_coverage_and_link_mismatch_are_incomplete() -> None:
    value = expected()
    wrong = value.link.model_copy(update={"attempt_id": UUID(int=9)})
    assert not Reader(context(wrong)).fetch(value).complete
    changed = value.model_copy(update={"expected_span_refs": ("root",)})
    assert not Reader(context(value.link)).fetch(changed).complete


def test_duplicate_expected_span_ids_are_rejected() -> None:
    value = expected()
    with pytest.raises(ValidationError):
        ExpectedTrace(
            link=value.link,
            trace_ref=value.trace_ref,
            expected_span_refs=("root", "root"),
        )


def test_mcp_event_stream_envelope_is_supported() -> None:
    reader = NeatlogsReadbackReader(SecretStr("test"))
    assert reader._json_response('event: message\ndata: {"jsonrpc":"2.0"}\n') == (
        '{"jsonrpc":"2.0"}'
    )
