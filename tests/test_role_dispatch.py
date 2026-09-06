import asyncio
import json
from unittest.mock import patch

import httpx
import pytest
from nexau import Agent
from nexau.archs.tool import Tool
from openai import OpenAI
from pydantic import ValidationError

from syndicate.budget_policy import BudgetCap, ProductRole
from syndicate.model_config import ModelSettings
from syndicate.nexau_runtime import dispatch_role
from syndicate.runtime_contracts import RoleDispatchRequest


def request(**changes: object) -> RoleDispatchRequest:
    value = RoleDispatchRequest(
        model=ModelSettings(
            endpoint="https://azure.example/openai/v1/", deployment="gpt-5.4-mini"
        ),
        role=ProductRole.TASK_JUDGE,
        prompt="Return a concise result.",
        budget=BudgetCap(max_tokens=10_000, max_seconds=2, max_spend_microusd=1),
        usage_ref="usage:role-dispatch",
        max_iterations=2,
        max_context_tokens=2_000,
        max_output_tokens=100,
        max_retries=0,
    )
    return value.model_copy(update=changes)


def tool() -> Tool:
    return Tool(
        name="read_status",
        description="Read the fixed public status.",
        input_schema={"type": "object", "properties": {}},
        implementation=lambda: "status: ok",
    )


def client(calls: list[dict[str, object]]) -> OpenAI:
    def respond(raw: httpx.Request) -> httpx.Response:
        payload = json.loads(raw.content)
        assert isinstance(payload, dict)
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "id": "resp1",
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "model": "gpt-5.4-mini",
                "output": [
                    {
                        "type": "message",
                        "id": "msg1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "complete",
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    return OpenAI(
        api_key="fixture",
        base_url="https://azure.example/openai/v1/",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )


def test_role_dispatch_uses_only_supplied_tool_and_returns_usage_ref() -> None:
    calls: list[dict[str, object]] = []
    supplied = tool()
    with client(calls) as value:
        receipt = asyncio.run(dispatch_role(request(), (supplied,), value))
    assert receipt.final_text == "complete"
    assert receipt.usage_ref == "usage:role-dispatch"
    assert supplied.disable_parallel
    assert len(calls) == 1
    assert '"read_status"' in json.dumps(calls[0])
    assert "run_shell_command" not in json.dumps(calls[0])


@pytest.mark.parametrize(
    "changes",
    [
        {"usage_ref": " "},
        {"max_iterations": 10},
        {"max_output_tokens": 2_000},
        {"max_retries": 1},
    ],
)
def test_role_dispatch_rejects_unbounded_requests(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RoleDispatchRequest.model_validate({**request().model_dump(), **changes})


def test_role_dispatch_timeout_preserves_no_artifact() -> None:
    async def never(*_: object, **__: object) -> str:
        await asyncio.sleep(2)
        return "unreachable"

    calls: list[dict[str, object]] = []
    with client(calls) as value, patch.object(Agent, "run_async", never):
        with pytest.raises(TimeoutError):
            asyncio.run(
                dispatch_role(
                    request(
                        budget=BudgetCap(
                            max_tokens=10_000, max_seconds=1, max_spend_microusd=1
                        )
                    ),
                    (),
                    value,
                )
            )


def test_role_dispatch_rejects_client_retries() -> None:
    with OpenAI(
        api_key="fixture",
        base_url="https://azure.example/openai/v1/",
        max_retries=1,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(500))
        ),
    ) as value:
        with pytest.raises(ValueError, match="zero retries"):
            asyncio.run(dispatch_role(request(), (), value))


def test_role_dispatch_provider_failure_has_one_attempt() -> None:
    calls: list[dict[str, object]] = []

    def fail(_: httpx.Request) -> httpx.Response:
        calls.append({})
        return httpx.Response(500, json={"error": {"message": "fixture"}})

    with OpenAI(
        api_key="fixture",
        base_url="https://azure.example/openai/v1/",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(fail)),
    ) as value:
        with pytest.raises(RuntimeError, match="agent execution"):
            asyncio.run(dispatch_role(request(), (), value))
    assert len(calls) == 1
