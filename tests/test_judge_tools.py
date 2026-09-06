import json

import pytest
from nexau import Tool
from test_task_judge import CITE, GRANT, Remote

from syndicate.models.evidence import EvidenceStatus, TraceQuery
from syndicate.models.judging import JudgeTool, ObservationStatus
from syndicate.services.evidence import EvidenceReader
from syndicate.services.judge_tools import build_judge_tools
from syndicate.services.judging import JudgeEvidence


def artifacts(result: dict[str, object]) -> dict[str, object]:
    observation = json.loads(str(result["result"]))
    assert isinstance(observation, dict)
    payload = json.loads(str(observation["artifacts_json"]))
    assert isinstance(payload, dict)
    return payload


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


def test_audit_tool_stays_unbound() -> None:
    with pytest.raises(ValueError, match="not available"):
        build_judge_tools(
            (JudgeTool.AUDIT,), JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
        )


def test_trace_tools_are_read_only_and_return_remote_results() -> None:
    manifest, search, span = tools()
    manifest_result = manifest.execute(
        run_id=str(CITE.run_id), trace_ref=CITE.trace_ref
    )
    observation = json.loads(manifest_result["result"])
    assert observation["status"] == ObservationStatus.SUCCESS
    assert artifacts(manifest_result)["span_count"] == 1
    search_result = search.execute(
        run_id=str(CITE.run_id), trace_ref=CITE.trace_ref, text="Denied"
    )
    assert artifacts(search_result)["span_refs"] == [CITE.span_ref]
    span_result = span.execute(
        run_id=str(CITE.run_id), trace_ref=CITE.trace_ref, span_ref=CITE.span_ref
    )
    spans = artifacts(span_result)["spans"]
    assert isinstance(spans, list)
    first = spans[0]
    assert isinstance(first, dict)
    output = first["output"]
    assert isinstance(output, dict)
    assert output["text"] == "Denied"


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


def test_forbidden_record_observation_tells_the_judge_to_stop() -> None:
    record = build_judge_tools(
        (JudgeTool.RECORD,), JudgeEvidence(EvidenceReader(Remote(), (GRANT,)))
    )[0]
    result = json.loads(
        record.execute(run_id=str(CITE.run_id), record_ref="harbor:opaque")["result"]
    )
    assert result["status"] == ObservationStatus.ERROR
    assert result["next_actions"] == ["stop: citation is not granted for this judge"]
    assert json.loads(result["artifacts_json"])["status"] == EvidenceStatus.FORBIDDEN
