import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, call, create_autospec, patch

from harbor.agents.factory import AgentFactory
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.verifier.factory import VerifierFactory
from pydantic import SecretStr
from test_runtime_request import request

from syndicate.benchmark import RunOutcome, VerifierReason, VerifierReceipt
from syndicate.harbor_adapter import (
    KEY_PATH,
    REQUEST_PATH,
    SyndicateHarborVerifier,
    SyndicateNexAUAgent,
)


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
            ExecResult(return_code=1),
        )
    )
    environment.upload_file = AsyncMock()
    environment.upload_dir = AsyncMock()
    agent = SyndicateNexAUAgent(
        logs_dir=tmp_path, request=request(), api_key=SecretStr("fixture")
    )

    async def exercise() -> None:
        await agent.setup(environment)
        await agent.run(agent.request.instruction, environment, create_autospec(object))

    asyncio.run(exercise())
    assert agent.cleanup_receipt is not None and agent.cleanup_receipt.complete
    assert environment.upload_dir.await_args.args[1] == "/run/syndicate/harness"
    assert environment.upload_file.await_args_list[0].args[1] == REQUEST_PATH
    assert environment.upload_file.await_args_list[1].args[1] == KEY_PATH
    assert environment.exec.await_args_list[:2] == [
        call(command="mkdir -p /run/syndicate", user="root"),
        call(
            command=(
                "chown -R 10001:10001 /run/syndicate && "
                "chmod 600 /run/syndicate/api-key"
            ),
            user="root",
        ),
    ]
    assert environment.exec.await_args_list[-5:] == [
        call(command="test ! -e /tests && test ! -e /solution", user="10001"),
        call(command="python -I -m syndicate.nexau_runtime", user="10001"),
        call(command="pkill -KILL -u 10001 || true", user="root"),
        call(command="pgrep -u 10001", user="root"),
        call(command="pgrep -u 10001", user="root"),
    ]


def test_native_import_paths_verify_only_after_settled_agent_cleanup(
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
            ExecResult(return_code=1),
        )
    )
    environment.upload_file = AsyncMock()
    environment.upload_dir = AsyncMock()
    agent = AgentFactory.create_agent_from_import_path(
        SyndicateNexAUAgent.import_path(),
        logs_dir=tmp_path,
        request=request(),
        api_key=SecretStr("fixture"),
    )
    assert isinstance(agent, SyndicateNexAUAgent)
    verifier = VerifierFactory.create_verifier_from_import_path(
        SyndicateHarborVerifier.import_path(),
        task=create_autospec(object),
        trial_paths=create_autospec(object),
        environment=environment,
    )
    assert isinstance(verifier, SyndicateHarborVerifier)
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
            result = await verifier.verify()
        verify.assert_awaited_once()
        call_args = verify.await_args
        assert call_args is not None
        assert call_args.args[-1] == agent.cleanup_receipt
        assert result.rewards == {"reward": 0.0}
        return verified

    receipt = asyncio.run(exercise())
    assert receipt is verified
    assert environment.exec.await_args_list[-1] == call(
        command="pgrep -u 10001", user="root"
    )


def test_native_verifier_refuses_to_call_harbor_without_cleanup_proof(
    tmp_path: Path,
) -> None:
    environment = create_autospec(BaseEnvironment, instance=True)
    verifier = SyndicateHarborVerifier(
        task=create_autospec(object),
        trial_paths=create_autospec(object),
        environment=environment,
    )

    with patch("syndicate.harbor_adapter.verify_with_harbor", AsyncMock()) as verify:
        result = asyncio.run(verifier.verify())

    assert result.rewards is None
    verify.assert_not_awaited()
