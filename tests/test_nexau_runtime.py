import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from nexau.archs.main_sub.execution.stop_reason import AgentStopReason
from openai import OpenAI
from pydantic import SecretStr

from syndicate.nexau_runtime import RuntimeStopped, run_in_container
from syndicate.runtime_contracts import RuntimeExit, RuntimeRequest


def respond(request: httpx.Request, calls: list[bytes]) -> httpx.Response:
    calls.append(request.content)
    assert b'"max_output_tokens":1000' in request.content.replace(b" ", b"")
    output: list[dict[str, object]] = (
        [
            {
                "type": "function_call",
                "id": f"fc{len(calls)}",
                "call_id": f"call{len(calls)}",
                "name": "run_shell_command",
                "arguments": json.dumps(
                    {
                        "command": "sleep 20"
                        if len(calls) == 1
                        else "printf shell-ok; test ! -e /tests",
                        "is_background": len(calls) == 1,
                    }
                ),
                "status": "completed",
            }
        ]
        if len(calls) <= 2
        else [
            {
                "type": "message",
                "id": "msg1",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {"type": "output_text", "text": "Completed.", "annotations": []}
                ],
            }
        ]
    )
    return httpx.Response(
        200,
        json={
            "id": f"resp{len(calls)}",
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "gpt-5.4-mini",
            "output": output,
            "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
        },
    )


def runtime_fixture(iterations: int) -> tuple[RuntimeRequest, OpenAI, list[bytes]]:
    calls: list[bytes] = []

    request = RuntimeRequest.model_validate_json(
        Path("/run/syndicate/request.json").read_bytes()
    )
    request = request.model_copy(update={"max_iterations": iterations})
    client = OpenAI(
        api_key="fixture",
        base_url=request.baseline.model.endpoint,
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda raw: respond(raw, calls))
        ),
    )
    return request, client, calls


@pytest.mark.skipif(os.getuid() != 10001, reason="dedicated runtime container required")
def test_iteration_limit_is_explicit() -> None:
    request, client, calls = runtime_fixture(1)
    with (
        patch("syndicate.nexau_runtime.OpenAI", return_value=client),
        asyncio.Runner() as runner,
    ):
        with pytest.raises(RuntimeStopped) as stopped:
            runner.run(run_in_container(request, SecretStr("fixture")))
    assert stopped.value.reason is AgentStopReason.MAX_ITERATIONS_REACHED
    assert not calls
    receipt = RuntimeExit.model_validate_json(
        Path("/logs/agent/runtime-exit.json").read_bytes()
    )
    assert receipt.stop_reason is AgentStopReason.MAX_ITERATIONS_REACHED


@pytest.mark.skipif(os.getuid() != 10001, reason="dedicated runtime container required")
def test_real_nexau_tool_cycle_without_model() -> None:
    request, client, calls = runtime_fixture(4)
    with (
        patch("syndicate.nexau_runtime.OpenAI", return_value=client),
        asyncio.Runner() as runner,
    ):
        result = runner.run(run_in_container(request, SecretStr("fixture")))
    receipt = RuntimeExit.model_validate_json(
        Path("/logs/agent/runtime-exit.json").read_bytes()
    )
    assert receipt.final_response == result == "Completed."
    assert len(calls) == 3
    assert all(b"gpt-5.4-mini" in call for call in calls)
    assert b"Background task started" in calls[1]
    assert b"shell-ok" in calls[2]
    assert Path("/logs/agent/nexau-trace.json").stat().st_size > 0
    assert "shell-ok" in Path("/logs/agent/shell-results.jsonl").read_text()
