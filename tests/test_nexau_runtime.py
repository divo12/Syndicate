import asyncio
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from nexau.archs.main_sub.execution.stop_reason import AgentStopReason
from openai import OpenAI
from pydantic import SecretStr

from syndicate.models.baseline import PromptVariables, prepare_baseline
from syndicate.models.budget import BudgetCap
from syndicate.models.model_config import ModelSettings
from syndicate.models.runtime import RuntimeRequest
from syndicate.models.shell import ShellExecution, ShellStatus
from syndicate.services.runtime import RuntimeStopped, run_on_controller

ROOT = Path(__file__).resolve().parents[1]


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

    request = RuntimeRequest(
        baseline=prepare_baseline(
            ROOT / "harnesses/seed",
            ROOT / "requirements.lock",
            ModelSettings(endpoint="https://example.com/", deployment="gpt-5.4-mini"),
            PromptVariables(
                date=date(2026, 9, 6), username="agent", working_directory="/app"
            ),
        ),
        instruction="Complete the task using the shell.",
        budget=BudgetCap(max_tokens=100_000, max_seconds=30, max_spend_microusd=1000),
        max_iterations=iterations,
        max_context_tokens=10_000,
        max_output_tokens=1000,
        shell_timeout_ms=1000,
    )
    client = OpenAI(
        api_key="fixture",
        base_url=request.baseline.model.endpoint,
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda raw: respond(raw, calls))
        ),
    )
    return request, client, calls


def test_iteration_limit_is_explicit() -> None:
    request, client, calls = runtime_fixture(1)
    with (
        patch("syndicate.services.runtime.OpenAI", return_value=client),
        patch(
            "syndicate.services.runtime.E2BShell", return_value=AsyncMock()
        ) as backend,
        asyncio.Runner() as runner,
    ):
        with pytest.raises(RuntimeStopped) as stopped:
            runner.run(
                run_on_controller(
                    request,
                    SecretStr("fixture"),
                    AsyncMock(),
                    harness_dir=ROOT / "harnesses/seed",
                    framework_lock=ROOT / "requirements.lock",
                )
            )
    assert stopped.value.reason is AgentStopReason.MAX_ITERATIONS_REACHED
    assert not calls
    backend.return_value.close.assert_awaited_once()


def test_real_nexau_tool_cycle_without_model() -> None:
    request, client, calls = runtime_fixture(4)
    sandbox = AsyncMock()
    shell = AsyncMock()
    shell.execute.side_effect = [
        ShellExecution(
            status=ShellStatus.BACKGROUND,
            exit_code=None,
            background_pid=123,
            capture_complete=False,
        ),
        ShellExecution(stdout="shell-ok", exit_code=0),
    ]
    with (
        patch("syndicate.services.runtime.OpenAI", return_value=client),
        patch("syndicate.services.runtime.E2BShell", return_value=shell) as backend,
        asyncio.Runner() as runner,
    ):
        result = runner.run(
            run_on_controller(
                request,
                SecretStr("fixture"),
                sandbox,
                harness_dir=ROOT / "harnesses/seed",
                framework_lock=ROOT / "requirements.lock",
            )
        )
    assert result == "Completed."
    backend.assert_called_once_with(sandbox, "/app")
    shell.close.assert_awaited_once()
    assert shell.execute.await_count == 2
    assert len(calls) == 3
    assert all(b"gpt-5.4-mini" in call for call in calls)
    assert b"Background task started" in calls[1]
    assert b"shell-ok" in calls[2]


def test_changed_baseline_never_opens_shell(tmp_path: Path) -> None:
    request, client, calls = runtime_fixture(4)
    lock = tmp_path / "requirements.lock"
    lock.write_text("changed framework lock\n")
    with client, patch("syndicate.services.runtime.E2BShell") as backend:
        with asyncio.Runner() as runner:
            with pytest.raises(ValueError, match="baseline differs"):
                runner.run(
                    run_on_controller(
                        request,
                        SecretStr("fixture"),
                        AsyncMock(),
                        harness_dir=ROOT / "harnesses/seed",
                        framework_lock=lock,
                    )
                )
    backend.assert_not_called()
    assert not calls


def test_provider_failure_closes_shell_without_retry() -> None:
    request, client, calls = runtime_fixture(4)
    client.close()
    attempts: list[bytes] = []

    def unavailable(raw: httpx.Request) -> httpx.Response:
        attempts.append(raw.content)
        return httpx.Response(500, json={"error": {"message": "unavailable"}})

    client = OpenAI(
        api_key="fixture",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(unavailable)),
    )
    shell = AsyncMock()
    with (
        patch("syndicate.services.runtime.OpenAI", return_value=client),
        patch("syndicate.services.runtime.E2BShell", return_value=shell),
        asyncio.Runner() as runner,
    ):
        with pytest.raises(RuntimeError):
            runner.run(
                run_on_controller(
                    request,
                    SecretStr("fixture"),
                    AsyncMock(),
                    harness_dir=ROOT / "harnesses/seed",
                    framework_lock=ROOT / "requirements.lock",
                )
            )
    assert len(attempts) == 1
    shell.close.assert_awaited_once()
    shell.execute.assert_not_awaited()
