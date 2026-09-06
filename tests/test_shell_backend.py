"""E2B transport, capture bounds, and controller-owned cleanup without cloud calls."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException, CommandResult
from e2b.sandbox_async.commands.command_handle import AsyncCommandHandle

from syndicate.adapters.e2b_shell import CaptureLimitError, E2BShell, _Capture
from syndicate.models.shell import ShellRequest, ShellStatus


def transport() -> tuple[Mock, AsyncMock]:
    sandbox = Mock(spec=AsyncSandbox)
    handle = AsyncMock(spec=AsyncCommandHandle)
    handle.pid = 42
    handle.wait.return_value = CommandResult(
        stdout="", stderr="", exit_code=0, error=None
    )
    sandbox.commands.run = AsyncMock(
        side_effect=[
            handle,
            CommandResult(stdout="", stderr="", exit_code=0, error=None),
            CommandExitException(stdout="", stderr="", exit_code=1, error=None),
        ]
    )
    return sandbox, handle


def test_command_runs_only_remotely_and_cleanup_preserves_vm() -> None:
    sandbox, handle = transport()

    async def run() -> None:
        shell = E2BShell(sandbox, "/work")
        result = await shell.execute(ShellRequest(command="printf hello"), 1000)
        assert result.status == ShellStatus.EXITED
        assert result.capture_complete
        await shell.close()
        await shell.close()
        assert shell.cleanup_complete

    asyncio.run(run())
    assert sandbox.commands.run.call_args_list[0].kwargs["user"] == "root"
    assert (
        "setpriv --reuid=10001 --regid=10001 --clear-groups --no-new-privs"
        in sandbox.commands.run.call_args_list[0].args[0]
    )
    handle.disconnect.assert_awaited_once()
    sandbox.kill.assert_not_called()


def test_capture_stops_at_limit() -> None:
    capture = _Capture(4)
    capture.append("abcd")
    with pytest.raises(CaptureLimitError):
        capture.append("overflow")
    assert capture.data == b"abcd"


def test_nonzero_exit_is_not_transport_failure() -> None:
    sandbox, handle = transport()
    handle.wait.side_effect = CommandExitException(
        stdout="", stderr="", exit_code=7, error=None
    )
    result = asyncio.run(
        E2BShell(sandbox, "/work").execute(ShellRequest(command="exit 7"), 1000)
    )
    assert result.exit_code == 7
    assert result.status == ShellStatus.EXITED


@pytest.mark.parametrize("error", [TimeoutError(), CaptureLimitError(), RuntimeError()])
def test_failed_stream_stops_task_uid(error: Exception) -> None:
    sandbox, handle = transport()
    handle.wait.side_effect = error
    shell = E2BShell(sandbox, "/work")
    result = asyncio.run(shell.execute(ShellRequest(command="yes"), 1000))
    assert not result.capture_complete
    assert shell.closed
    assert sandbox.commands.run.call_args_list[-1].kwargs["user"] == "root"


def test_finished_background_jobs_are_released() -> None:
    sandbox, handle = transport()
    sandbox.commands.run.side_effect = None
    sandbox.commands.run.return_value = handle

    async def run() -> None:
        shell = E2BShell(sandbox, "/work")
        for _ in range(100):
            result = await shell.execute(
                ShellRequest(command="true", is_background=True), 1000
            )
            assert result.background_pid == 42
        await asyncio.gather(*tuple(shell.pending))
        await asyncio.sleep(0)
        assert not shell.pending

    asyncio.run(run())


def test_cancellation_settles_remote_spawn_and_cleans_uid() -> None:
    sandbox, handle = transport()

    async def wait() -> CommandResult:
        await asyncio.sleep(20)
        return CommandResult(stdout="", stderr="", exit_code=0, error=None)

    handle.wait.side_effect = wait

    async def run() -> None:
        shell = E2BShell(sandbox, "/work")
        task = asyncio.create_task(
            shell.execute(ShellRequest(command="sleep 20"), 1000)
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert shell.cleanup_complete

    asyncio.run(run())


def test_cleanup_failure_blocks_verifier() -> None:
    sandbox, _ = transport()
    sandbox.commands.run.side_effect = RuntimeError("unavailable")
    shell = E2BShell(sandbox, "/work")
    with pytest.raises(RuntimeError):
        asyncio.run(shell.close())
    assert not shell.cleanup_complete


def test_disconnect_failure_still_attempts_uid_cleanup() -> None:
    sandbox, handle = transport()
    handle.disconnect.side_effect = RuntimeError("disconnect failed")
    sandbox.commands.run.side_effect = None

    async def run() -> None:
        shell = E2BShell(sandbox, "/work")

        async def started() -> AsyncCommandHandle:
            return handle

        spawn = asyncio.create_task(started())
        shell.spawns.add(spawn)
        await spawn
        with pytest.raises(RuntimeError, match="disconnect failed"):
            await shell.close()
        assert not shell.cleanup_complete
        sandbox.commands.run.assert_awaited_once_with(
            "pkill -KILL -u 10001 || test $? = 1", user="root", timeout=5
        )

    asyncio.run(run())


def test_close_waits_for_inflight_startup() -> None:
    sandbox, handle = transport()

    async def run() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def spawn() -> AsyncCommandHandle:
            started.set()
            await release.wait()
            return handle

        sandbox.commands.run.side_effect = [
            spawn(),
            CommandResult(stdout="", stderr="", exit_code=0, error=None),
            CommandExitException(stdout="", stderr="", exit_code=1, error=None),
        ]
        original = sandbox.commands.run

        async def transport_call(*args: object, **kwargs: object) -> object:
            result = await original(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return await result
            return result

        sandbox.commands.run = transport_call
        shell = E2BShell(sandbox, "/work")
        execute = asyncio.create_task(shell.execute(ShellRequest(command="true"), 1000))
        await started.wait()
        close = asyncio.create_task(shell.close())
        await asyncio.sleep(0)
        assert not shell.cleanup_complete
        release.set()
        await close
        with pytest.raises(RuntimeError, match="closed"):
            await execute
        assert shell.cleanup_complete

    asyncio.run(run())


@pytest.mark.parametrize("uid", [0, -1, True])
def test_reject_privileged_or_invalid_uid(uid: int) -> None:
    sandbox, _ = transport()
    with pytest.raises(ValueError):
        E2BShell(sandbox, "/work", uid=uid)
