import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, create_autospec, patch
from uuid import uuid4

import pytest
from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandResult
from harbor.agents.factory import AgentFactory
from harbor.environments.base import BaseEnvironment
from harbor.environments.e2b import E2BEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import SecretStr
from test_runtime_request import request

from syndicate.adapters.harbor_adapter import SyndicateNexAUAgent
from syndicate.adapters.harbor_agent import CleanupReceipt
from syndicate.services.stock import ControllerTrialBinding, load_cleanup_receipt


def test_factory_uses_harbors_sandbox_without_uploads(tmp_path: Path) -> None:
    environment = create_autospec(E2BEnvironment, instance=True)
    sandbox = create_autospec(AsyncSandbox, instance=True)
    environment._sandbox = sandbox
    sandbox.commands.run = AsyncMock(
        return_value=CommandResult(exit_code=0, stdout="", stderr="", error=None)
    )
    agent = AgentFactory.create_agent_from_import_path(
        SyndicateNexAUAgent.import_path(),
        logs_dir=tmp_path,
        request=request(),
        api_key=SecretStr("fixture"),
        harness_dir=tmp_path / "harness",
        framework_lock=tmp_path / "lock",
    )
    assert isinstance(agent, SyndicateNexAUAgent)
    with patch("syndicate.adapters.harbor_adapter.HarborAgent") as lifecycle:
        lifecycle.return_value.run = AsyncMock(
            return_value=CleanupReceipt(uid=10001, complete=True)
        )

        async def exercise() -> None:
            await agent.setup(environment)
            await agent.run(agent.request.instruction, environment, AgentContext())

        asyncio.run(exercise())
        lifecycle.assert_called_once_with(
            sandbox,
            harness_dir=tmp_path / "harness",
            framework_lock=tmp_path / "lock",
        )
        lifecycle.return_value.run.assert_awaited_once_with(
            agent.request, agent.api_key
        )
    assert agent.cleanup_receipt == CleanupReceipt(uid=10001, complete=True)
    environment.upload_dir.assert_not_called()
    environment.upload_file.assert_not_called()
    environment.exec.assert_not_called()
    command = sandbox.commands.run.call_args.args[0]
    assert "10001" in command and "/app" in command
    assert "chown" not in command and "chmod" not in command


@pytest.mark.parametrize("failure", ["instruction", "probe", "runtime"])
def test_failed_admission_or_execution_has_no_receipt(
    tmp_path: Path, failure: str
) -> None:
    environment = create_autospec(E2BEnvironment, instance=True)
    environment._sandbox = create_autospec(AsyncSandbox, instance=True)
    environment._sandbox.commands.run = AsyncMock(
        return_value=CommandResult(exit_code=1, stdout="", stderr="", error=None)
    )
    agent = SyndicateNexAUAgent(tmp_path, request(), SecretStr("fixture"))
    with patch("syndicate.adapters.harbor_adapter.HarborAgent") as lifecycle:
        lifecycle.return_value.run = AsyncMock(side_effect=RuntimeError("failed"))

        async def exercise() -> None:
            if failure == "probe":
                await agent.setup(environment)
            else:
                await agent.run(
                    "wrong" if failure == "instruction" else agent.request.instruction,
                    environment,
                    AgentContext(),
                )

        with pytest.raises((ValueError, RuntimeError)):
            asyncio.run(exercise())
    assert agent.cleanup_receipt is None


@pytest.mark.parametrize("started", [False, True])
def test_requires_started_e2b_environment(tmp_path: Path, started: bool) -> None:
    environment = create_autospec(
        BaseEnvironment if started else E2BEnvironment, instance=True
    )
    environment._sandbox = None
    agent = SyndicateNexAUAgent(tmp_path, request(), SecretStr("fixture"))
    with pytest.raises((ValueError, RuntimeError)):
        asyncio.run(agent.setup(environment))


def test_harbor_deadline_aborts_instead_of_becoming_verifier_eligible(
    tmp_path: Path,
) -> None:
    environment = create_autospec(E2BEnvironment, instance=True)
    environment._sandbox = create_autospec(AsyncSandbox, instance=True)
    agent = SyndicateNexAUAgent(tmp_path, request(), SecretStr("fixture"))

    with patch("syndicate.adapters.harbor_adapter.HarborAgent") as lifecycle:

        async def run_controller(*args: object) -> CleanupReceipt:
            await asyncio.sleep(10)
            return CleanupReceipt(complete=True)

        lifecycle.return_value.run = run_controller

        async def exercise() -> None:
            with pytest.raises(RuntimeError, match="handoff blocked"):
                await asyncio.wait_for(
                    agent.run(agent.request.instruction, environment, AgentContext()),
                    0.01,
                )

        asyncio.run(exercise())
    assert agent.cleanup_receipt is None


@pytest.mark.parametrize("failed", [False, True])
def test_stock_receipt_requires_successful_controller_run(
    tmp_path: Path, failed: bool
) -> None:
    environment = create_autospec(E2BEnvironment, instance=True)
    environment._sandbox = create_autospec(AsyncSandbox, instance=True)
    agent = SyndicateNexAUAgent(tmp_path, request(), SecretStr("fixture"))
    binding = ControllerTrialBinding(
        operation_id=uuid4(),
        attempt_id=uuid4(),
        run_id=uuid4(),
        task_id="task-a-1",
    )
    agent.bind_controller_receipt(binding, tmp_path)
    with patch("syndicate.adapters.harbor_adapter.HarborAgent") as lifecycle:
        lifecycle.return_value.run = AsyncMock(
            return_value=CleanupReceipt(complete=True),
            side_effect=RuntimeError("controller failed") if failed else None,
        )
        execution = agent.run(agent.request.instruction, environment, AgentContext())
        if failed:
            with pytest.raises(RuntimeError, match="handoff blocked"):
                asyncio.run(execution)
            assert not tuple(tmp_path.rglob("cleanup.json"))
        else:
            asyncio.run(execution)
            receipt = load_cleanup_receipt(binding, tmp_path)
            assert receipt.cleanup == agent.cleanup_receipt
            assert receipt.written_at.utcoffset() is not None
