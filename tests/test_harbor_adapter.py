import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, call, create_autospec, patch

from harbor.agents.factory import AgentFactory
from harbor.environments.base import BaseEnvironment, ExecResult
from pydantic import SecretStr
from test_runtime_request import request

from syndicate.benchmark import RunOutcome, VerifierReason, VerifierReceipt
from syndicate.harbor_adapter import KEY_PATH, REQUEST_PATH, SyndicateNexAUAgent


def test_adapter_stages_transient_runtime_inputs_and_cleans_before_returning(
    tmp_path: Path,
) -> None:
    environment = create_autospec(BaseEnvironment, instance=True)
    environment.exec = AsyncMock(
        side_effect=(
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=1),
            ExecResult(return_code=1),
        )
    )
    environment.upload_file = AsyncMock()
    agent = SyndicateNexAUAgent(
        logs_dir=tmp_path, request=request(), api_key=SecretStr("fixture")
    )

    async def exercise() -> None:
        await agent.setup(environment)
        await agent.run(agent.request.instruction, environment, create_autospec(object))

    asyncio.run(exercise())
    assert agent.cleanup_receipt is not None and agent.cleanup_receipt.complete
    assert environment.upload_file.await_args_list[0].args[1] == REQUEST_PATH
    assert environment.upload_file.await_args_list[1].args[1] == KEY_PATH
    assert environment.exec.await_args_list[-5:] == [
        call(command="test ! -e /tests && test ! -e /solution", user="10001"),
        call(command="python -I -m syndicate.nexau_runtime", user="10001"),
        call(command="pkill -KILL -u 10001 || true", user="root"),
        call(command="pgrep -u 10001", user="root"),
        call(command="pgrep -u 10001", user="root"),
    ]


def test_native_import_path_registration_and_verification_wait_for_cleanup(
    tmp_path: Path,
) -> None:
    environment = create_autospec(BaseEnvironment, instance=True)
    environment.exec = AsyncMock(
        side_effect=(
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=1),
            ExecResult(return_code=1),
        )
    )
    environment.upload_file = AsyncMock()
    agent = AgentFactory.create_agent_from_import_path(
        SyndicateNexAUAgent.import_path(),
        logs_dir=tmp_path,
        request=request(),
        api_key=SecretStr("fixture"),
    )
    assert isinstance(agent, SyndicateNexAUAgent)
    verified = VerifierReceipt(
        outcome=RunOutcome.FAIL,
        reason=VerifierReason.FAILED,
        reward=0.0,
        raw_result_ref="harbor:opaque",
    )

    async def exercise() -> VerifierReceipt:
        await agent.setup(environment)
        await agent.run(agent.request.instruction, environment, create_autospec(object))
        with patch(
            "syndicate.harbor_adapter.verify_with_harbor",
            AsyncMock(return_value=verified),
        ) as verify:
            receipt = await agent.verify(
                create_autospec(object),
                create_autospec(object),
                environment,
                "harbor:opaque",
            )
        verify.assert_awaited_once()
        call_args = verify.await_args
        assert call_args is not None
        assert call_args.args[-1] == agent.cleanup_receipt
        return receipt

    receipt = asyncio.run(exercise())
    assert receipt is verified
    assert environment.exec.await_args_list[-1] == call(
        command="pgrep -u 10001", user="root"
    )
