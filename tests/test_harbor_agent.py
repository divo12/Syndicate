import asyncio
import inspect
from unittest.mock import AsyncMock, call, create_autospec

import pytest
from harbor.environments.base import BaseEnvironment, ExecResult

from syndicate.harbor_agent import HarborAgent, runtime_command


def test_runtime_command_has_no_dead_path_argument() -> None:
    assert runtime_command() == "python -I -m syndicate.nexau_runtime"
    assert not inspect.signature(runtime_command).parameters


def test_lifecycle_hides_verifier_paths_and_settles_uid_before_returning() -> None:
    environment = create_autospec(BaseEnvironment, instance=True)
    environment.exec = AsyncMock(
        side_effect=(
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=0),
            ExecResult(return_code=1),
            ExecResult(return_code=1),
        )
    )
    receipt = asyncio.run(HarborAgent(environment, cleanup_timeout_ms=100).run("true"))
    assert receipt.complete
    assert environment.exec.await_args_list == [
        call(command="test ! -e /tests && test ! -e /solution", user="10001"),
        call(command="true", user="10001"),
        call(command="pkill -KILL -u 10001 || true", user="root"),
        call(command="pgrep -u 10001", user="root"),
        call(command="pgrep -u 10001", user="root"),
    ]


def test_failed_hidden_path_probe_blocks_agent_execution() -> None:
    environment = create_autospec(BaseEnvironment, instance=True)
    environment.exec = AsyncMock(return_value=ExecResult(return_code=1))

    with pytest.raises(PermissionError, match="Hidden"):
        asyncio.run(HarborAgent(environment).run("true"))

    assert environment.exec.await_args_list == [
        call(command="test ! -e /tests && test ! -e /solution", user="10001")
    ]
