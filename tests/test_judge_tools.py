import json

from nexau import Tool  # type: ignore[import-untyped]
from test_task_judge import CITE, GRANT, Remote

from syndicate.evidence import EvidenceReader
from syndicate.evidence_contracts import TraceQuery
from syndicate.judge_contracts import JudgeTool
from syndicate.judge_tools import build_judge_tools
from syndicate.judging import JudgeEvidence


def tools() -> tuple[Tool, ...]:
    evidence = JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
    return build_judge_tools(
        (JudgeTool.MANIFEST, JudgeTool.SEARCH, JudgeTool.SPAN), evidence
    )


def test_only_controller_bound_read_tools_are_exposed() -> None:
    assert tuple(tool.name for tool in tools()) == (
        JudgeTool.MANIFEST,
        JudgeTool.SEARCH,
        JudgeTool.SPAN,
    )


def test_trace_tools_are_read_only_and_return_remote_results() -> None:
    manifest, search, span = tools()
    manifest_result = manifest.execute(
        run_id=str(CITE.run_id), trace_ref=CITE.trace_ref
    )
    assert json.loads(manifest_result["result"])["span_count"] == 1
    search_result = search.execute(
        run_id=str(CITE.run_id), trace_ref=CITE.trace_ref, text="Denied"
    )
    assert json.loads(search_result["result"])["span_refs"] == [CITE.span_ref]
    span_result = span.execute(
        run_id=str(CITE.run_id), trace_ref=CITE.trace_ref, span_ref=CITE.span_ref
    )
    assert json.loads(span_result["result"])["spans"][0]["output"]["text"] == "Denied"


def test_span_tool_records_only_complete_read_ids() -> None:
    evidence = JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
    span = build_judge_tools((JudgeTool.SPAN,), evidence)[0]
    span.execute(
        run_id=str(CITE.run_id),
        trace_ref=CITE.trace_ref,
        span_ref=CITE.span_ref,
        max_chars=5,
    )
    assert evidence.examined == ()
    span.execute(
        run_id=str(CITE.run_id),
        trace_ref=CITE.trace_ref,
        span_ref=CITE.span_ref,
        max_chars=20,
    )
    assert evidence.examined == (CITE,)


def test_search_schema_stays_aligned_with_typed_query() -> None:
    search = build_judge_tools(
        (JudgeTool.SEARCH,), JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
    )[0]
    assert search.input_schema == TraceQuery.model_json_schema()
