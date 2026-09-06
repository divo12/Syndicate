"""Lifecycle proof follows successful controller execution, never failed probes."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest
from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException, CommandResult
from pydantic import SecretStr, ValidationError
from test_runtime_request import request

from syndicate.adapters.harbor_agent import CleanupReceipt, HarborAgent


def agent() -> HarborAgent:
    sandbox = Mock(spec=AsyncSandbox)
    sandbox.commands.run = AsyncMock(
        return_value=CommandResult(
            stdout="",
            stderr="",
            exit_code=0,
            error=None,
        )
    )
    return HarborAgent(sandbox, harness_dir=Path("seed"), framework_lock=Path("lock"))


def test_success_proof_uses_existing_controller_runner() -> None:
    runner = agent()
    value, key = request(), SecretStr("fixture")
    with patch(
        "syndicate.adapters.harbor_agent.run_on_controller", new_callable=AsyncMock
    ) as run:
        receipt = asyncio.run(runner.run(value, key))
    assert receipt == CleanupReceipt(complete=True)
    run.assert_awaited_once_with(
        value,
        key,
        runner.sandbox,
        harness_dir=Path("seed"),
        framework_lock=Path("lock"),
    )


@pytest.mark.parametrize("exit_code", [1, 2, 127])
def test_probe_failure_never_runs_agent(exit_code: int) -> None:
    runner = agent()
    with (
        patch.object(
            runner.sandbox.commands,
            "run",
            new_callable=AsyncMock,
            side_effect=CommandExitException(
                stdout="", stderr="", exit_code=exit_code, error=None
            ),
        ),
        patch(
            "syndicate.adapters.harbor_agent.run_on_controller", new_callable=AsyncMock
        ) as run,
    ):
        with pytest.raises(PermissionError):
            asyncio.run(runner.run(request(), SecretStr("fixture")))
    run.assert_not_awaited()


@pytest.mark.parametrize(
    "failure", [RuntimeError("runtime failed"), RuntimeError("cleanup failed")]
)
def test_failed_execution_or_cleanup_has_no_success_receipt(
    failure: RuntimeError,
) -> None:
    runner = agent()
    with patch(
        "syndicate.adapters.harbor_agent.run_on_controller",
        new_callable=AsyncMock,
        side_effect=failure,
    ):
        with pytest.raises(RuntimeError, match=str(failure)):
            asyncio.run(runner.run(request(), SecretStr("fixture")))


def test_wrong_cleanup_uid_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CleanupReceipt(uid=10002, complete=True)  # type: ignore[arg-type]
