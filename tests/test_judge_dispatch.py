import asyncio
import json

import httpx
import pytest
from openai import OpenAI
from test_judging import draft, generator, request
from test_task_judge import CITE, GRANT, LINK, RUN, Remote, report

from syndicate.benchmark import (
    RunOutcome,
    RunReceipt,
    VerifierReason,
    VerifierReceipt,
)
from syndicate.budget_policy import BudgetCap, ProductRole
from syndicate.evidence import EvidenceReader
from syndicate.evidence_contracts import RecordCitation, RunEvidenceGrant
from syndicate.harbor_agent import CleanupReceipt
from syndicate.judge_contracts import JudgeAttempt, JudgeSpec, ReportStatus
from syndicate.judge_dispatch import attempt_from_receipt, dispatch_judge, role_tools
from syndicate.judging import JudgeEvidence, JudgeRegistry
from syndicate.model_config import ModelSettings
from syndicate.runtime_contracts import RoleDispatchReceipt, RoleDispatchRequest

ANCHOR = RecordCitation(run_id=RUN, record_ref="verifier:trusted-1")


def spec() -> JudgeSpec:
    return JudgeRegistry().generate(request(), generator(draft()))


def reader() -> EvidenceReader:
    verifier = VerifierReceipt(
        outcome=RunOutcome.FAIL,
        reason=VerifierReason.FAILED,
        reward=0.0,
        raw_result_ref=ANCHOR.record_ref,
    )
    receipt = RunReceipt(
        operation_id=LINK.operation_id,
        attempt_id=LINK.attempt_id,
        run_id=RUN,
        task_id=LINK.task_id,
        cleanup_complete=True,
        cleanup=CleanupReceipt(uid=10001, complete=True),
        outcome=RunOutcome.FAIL,
        verifier=verifier,
    )
    grant = RunEvidenceGrant(
        operation_id=LINK.operation_id,
        attempt_id=LINK.attempt_id,
        run_id=RUN,
        task_id=LINK.task_id,
        record_ref=ANCHOR.record_ref,
    )
    return EvidenceReader(Remote(), (GRANT,), (grant,), (receipt,))


def role_request() -> RoleDispatchRequest:
    return RoleDispatchRequest(
        model=ModelSettings(
            endpoint="https://azure.example/openai/v1/", deployment="gpt-5.4-mini"
        ),
        role=ProductRole.TASK_JUDGE,
        prompt=spec().prompt,
        budget=BudgetCap(max_tokens=16_000, max_seconds=2, max_spend_microusd=1),
        usage_ref="usage:reserved",
        max_iterations=3,
        max_context_tokens=5_000,
        max_output_tokens=100,
        max_retries=0,
    )


def client(calls: list[dict[str, object]]) -> OpenAI:
    def respond(raw: httpx.Request) -> httpx.Response:
        payload = json.loads(raw.content)
        assert isinstance(payload, dict)
        calls.append(payload)
        output: list[dict[str, object]]
        if len(calls) == 1:
            output = [
                {
                    "type": "function_call",
                    "id": "call1",
                    "call_id": "call1",
                    "name": "read_span_context",
                    "arguments": json.dumps(
                        {
                            "run_id": str(CITE.run_id),
                            "trace_ref": CITE.trace_ref,
                            "span_ref": CITE.span_ref,
                            "before": 0,
                            "after": 0,
                            "max_chars": 100,
                        }
                    ),
                    "status": "completed",
                }
            ]
        else:
            output = [
                {
                    "type": "message",
                    "id": "msg1",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": report().model_dump_json(),
                            "annotations": [],
                        }
                    ],
                }
            ]
        return httpx.Response(
            200,
            json={
                "id": "resp1",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.4-mini",
                "output": output,
            },
        )

    return OpenAI(
        api_key="fixture",
        base_url="https://azure.example/openai/v1/",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )


def test_final_text_becomes_single_judge_attempt_with_reserved_usage() -> None:
    evidence = JudgeEvidence(reader())
    receipt = RoleDispatchReceipt(
        final_text='{"status":"incomplete"}',
        usage_ref="usage:reserved",
        stop_reason=None,
    )
    attempt = attempt_from_receipt(receipt, "usage:reserved", evidence)
    assert attempt == JudgeAttempt(output_json='{"status":"incomplete"}', examined=())


def test_usage_reference_mismatch_is_rejected_before_report_admission() -> None:
    receipt = RoleDispatchReceipt(
        final_text="{}", usage_ref="usage:other", stop_reason=None
    )
    with pytest.raises(ValueError, match="usage"):
        attempt_from_receipt(receipt, "usage:reserved", JudgeEvidence(reader()))


def test_role_toolset_contains_only_controller_read_tools() -> None:
    tools = role_tools(spec(), JudgeEvidence(reader()))
    assert tuple(tool.name for tool in tools) == (
        "get_trace_manifest",
        "search_trajectory",
        "read_span_context",
    )


def test_actual_nexau_dispatch_uses_only_controller_read_tools() -> None:
    calls: list[dict[str, object]] = []
    with client(calls) as value:
        result = asyncio.run(
            dispatch_judge(spec(), reader(), (ANCHOR,), role_request(), value)
        )
    assert result.status is ReportStatus.COMPLETE
    assert result.usage_ref == "usage:reserved"
    request_json = json.dumps(calls[0])
    assert "run_shell_command" not in request_json
    assert "read_span_context" in request_json


def test_role_failure_becomes_accounted_incomplete_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed(*_: object) -> RoleDispatchReceipt:
        raise TimeoutError

    monkeypatch.setattr("syndicate.judge_dispatch.dispatch_role", failed)
    with client([]) as value:
        result = asyncio.run(
            dispatch_judge(spec(), reader(), (ANCHOR,), role_request(), value)
        )
    assert result.usage_ref == "usage:reserved"
    assert result.status is ReportStatus.INCOMPLETE
