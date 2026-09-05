"""Run inside an unprivileged Linux container for actual subprocess coverage."""

import asyncio
import os
from pathlib import Path

import pytest

from syndicate.shell import ShellRequest, ShellStatus
from syndicate.shell_backend import ContainerShell

CONTAINER_USER = Path("/.dockerenv").is_file() and os.getuid() != 0
container_only = pytest.mark.skipif(
    not CONTAINER_USER, reason="unprivileged container required"
)


def test_host_or_root_refused(tmp_path: Path) -> None:
    if CONTAINER_USER:
        pytest.skip("guard exercised on host/root")
    with pytest.raises(RuntimeError, match="unprivileged"):
        ContainerShell(tmp_path)


@container_only
def test_real_output_directory_and_exit(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()

    async def run() -> None:
        backend = ContainerShell(tmp_path)
        result = await backend.execute(
            ShellRequest(
                command="pwd; printf raw; printf err >&2; exit 3",
                dir_path="sub",
            ),
            1000,
        )
        assert result.stdout == f"{tmp_path}/sub\nraw"
        assert result.stderr == "err"
        assert result.exit_code == 3
        assert result.capture_complete
        await backend.close()
        await backend.close()

    asyncio.run(run())


@container_only
@pytest.mark.parametrize("directory", ["/", "../", "missing", "escape"])
def test_cwd_confinement(tmp_path: Path, directory: str) -> None:
    (tmp_path / "escape").symlink_to("/")

    async def run() -> None:
        backend = ContainerShell(tmp_path)
        with pytest.raises((ValueError, OSError)):
            await backend.execute(
                ShellRequest(command="true", dir_path=directory), 1000
            )
        await backend.close()

    asyncio.run(run())


@container_only
def test_timeout_retains_raw_and_signal(tmp_path: Path) -> None:
    async def run() -> None:
        backend = ContainerShell(tmp_path)
        result = await backend.execute(
            ShellRequest(command="printf before; sleep 20"), 100
        )
        assert result.status is ShellStatus.TIMEOUT
        assert result.stdout == "before"
        assert result.exit_code == -9
        await backend.close()

    asyncio.run(run())


@container_only
def test_background_deadline_and_close(tmp_path: Path) -> None:
    async def run() -> None:
        backend = ContainerShell(tmp_path)
        result = await backend.execute(
            ShellRequest(command="sleep 20", is_background=True), 100
        )
        assert result.status is ShellStatus.BACKGROUND
        assert not result.capture_complete
        assert result.background_pid is not None
        await asyncio.sleep(0.2)
        with pytest.raises(ProcessLookupError):
            os.kill(result.background_pid, 0)
        await backend.close()
        with pytest.raises(RuntimeError, match="closed"):
            await backend.execute(ShellRequest(command="true"), 1000)

    asyncio.run(run())


@container_only
def test_output_limit_is_explicit(tmp_path: Path) -> None:
    async def run() -> None:
        backend = ContainerShell(tmp_path, capture_limit_bytes=1024)
        result = await backend.execute(ShellRequest(command="yes output"), 1000)
        assert len(result.stdout.encode()) == 1024
        assert not result.capture_complete
        assert result.error == "Raw capture limit reached"
        await backend.close()

    asyncio.run(run())


@container_only
def test_cancel_reaps_group(tmp_path: Path) -> None:
    async def run() -> None:
        backend = ContainerShell(tmp_path)
        task = asyncio.create_task(
            backend.execute(
                ShellRequest(
                    command=f"echo $$ > {tmp_path}/pid; sleep 20",
                ),
                10000,
            )
        )
        while not (tmp_path / "pid").exists():
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        pid = int((tmp_path / "pid").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    asyncio.run(run())
