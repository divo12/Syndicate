"""No host-shell fallback: the binding can execute only its supplied sandbox."""

import asyncio

import pytest
from pydantic import ValidationError

from syndicate.shell import (
    ShellBinding,
    ShellExecution,
    ShellRequest,
    ShellStatus,
    truncate_output,
)


class Sandbox:
    def __init__(self, result: ShellExecution, *, delay: float = 0) -> None:
        self.result = result
        self.delay = delay
        self.requests: list[ShellRequest] = []
        self.closed = False

    async def execute(self, request: ShellRequest, timeout_ms: int) -> ShellExecution:
        self.requests.append(request)
        await asyncio.sleep(self.delay)
        return self.result

    async def close(self) -> None:
        self.closed = True


def test_foreground_preserves_request_raw_and_visible_output() -> None:
    raw = ShellExecution(stdout="hello", stderr="warning", exit_code=3)
    sandbox = Sandbox(raw)
    request = ShellRequest(
        command="printf hello", dir_path="subdir", description="test"
    )

    async def run() -> None:
        async with ShellBinding(sandbox, timeout_ms=1000) as binding:
            result = await binding.run_shell_command(request)
            assert result.execution is raw
            assert result.content == "Output: hello\nwarning\nExit Code: 3"
            assert result.return_display == "hello\nwarning"
        assert sandbox.closed

    asyncio.run(run())
    assert sandbox.requests == [request]


def test_background_keeps_pid_and_capture_paths() -> None:
    raw = ShellExecution(
        status=ShellStatus.BACKGROUND,
        exit_code=None,
        background_pid=42,
        capture_complete=False,
        stdout_file="/tmp/job.out",
        stderr_file="/tmp/job.err",
    )
    sandbox = Sandbox(raw)
    result = asyncio.run(
        ShellBinding(sandbox, timeout_ms=1000).run_shell_command(
            ShellRequest(command="server", is_background=True),
        )
    )
    assert result.content == "Background task started (pid: 42)."
    assert result.execution.stdout_file == "/tmp/job.out"
    assert not result.execution.capture_complete


def test_deadline_closes_sandbox_and_marks_incomplete() -> None:
    sandbox = Sandbox(ShellExecution(exit_code=0), delay=1)
    result = asyncio.run(
        ShellBinding(sandbox, timeout_ms=1).run_shell_command(
            ShellRequest(command="sleep 100"),
        )
    )
    assert sandbox.closed
    assert result.execution.status is ShellStatus.TIMEOUT
    assert not result.execution.capture_complete
    assert "Timeout:" in result.content


def test_cancellation_closes_sandbox() -> None:
    sandbox = Sandbox(ShellExecution(exit_code=0), delay=10)

    async def run() -> None:
        binding = ShellBinding(sandbox, timeout_ms=1000)
        task = asyncio.create_task(
            binding.run_shell_command(ShellRequest(command="wait"))
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sandbox.closed

    asyncio.run(run())


@pytest.mark.parametrize("command", ["", " ", "\n"])
def test_reject_empty_commands(command: str) -> None:
    with pytest.raises(ValidationError):
        ShellRequest(command=command)


@pytest.mark.parametrize("timeout", [0, -1])
def test_no_unbounded_timeout(timeout: int) -> None:
    with pytest.raises(ValueError):
        ShellBinding(Sandbox(ShellExecution(exit_code=0)), timeout_ms=timeout)


def test_seed_truncation_preserves_threshold_and_tail() -> None:
    assert truncate_output("a" * 4_000_000) == "a" * 4_000_000
    huge = "a" * 4_000_001
    assert truncate_output(huge).endswith("..." + "a" * 4000)
    lines = ("b" * 2000 + "\n") * 2100
    shortened = truncate_output(lines)
    assert "last 1000 of 2101 lines" in shortened
    assert "[LINE WIDTH TRUNCATED]" in shortened


def test_invalid_background_receipt_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ShellExecution(status=ShellStatus.BACKGROUND, exit_code=None)


def test_timeout_retains_partial_output() -> None:
    raw = ShellExecution(status=ShellStatus.TIMEOUT, stdout="partial", exit_code=None)
    result = asyncio.run(
        ShellBinding(Sandbox(raw), timeout_ms=1000).run_shell_command(
            ShellRequest(command="slow"),
        )
    )
    assert result.execution.stdout == "partial"
    assert result.return_display == "partial"
    assert "Timeout:" in result.content


@pytest.mark.parametrize(
    ("code", "display"),
    [
        (0, "(empty)"),
        (2, "Command exited with code: 2"),
    ],
)
def test_empty_output_preserves_seed_display(code: int, display: str) -> None:
    result = asyncio.run(
        ShellBinding(
            Sandbox(ShellExecution(exit_code=code)), timeout_ms=1000
        ).run_shell_command(ShellRequest(command="true"))
    )
    assert result.return_display == display
    assert result.content.startswith("Output: (empty)")


def test_real_container_binding_and_timeout_cleanup() -> None:
    """Opt-in smoke test; the production Harbor backend is supplied by P08."""
    import os
    import subprocess

    if os.environ.get("SYNDICATE_DOCKER_TEST") != "1":
        pytest.skip("set SYNDICATE_DOCKER_TEST=1 for the no-model Docker smoke test")
    container = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--label",
            f"ao.session={os.environ['AO_SESSION_ID']}",
            "-w",
            "/work",
            "public.ecr.aws/docker/library/python:3.13-slim-bookworm",
            "sleep",
            "infinity",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    class DockerSandbox:
        def __init__(self) -> None:
            self.processes: list[asyncio.subprocess.Process] = []

        async def execute(
            self, request: ShellRequest, timeout_ms: int
        ) -> ShellExecution:
            process = await asyncio.create_subprocess_exec(
                "docker",
                "exec",
                "-w",
                "/work",
                container,
                "bash",
                "-c",
                request.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self.processes.append(process)
            stdout, stderr = await process.communicate()
            return ShellExecution(
                stdout=stdout.decode(),
                stderr=stderr.decode(),
                exit_code=process.returncode,
            )

        async def close(self) -> None:
            subprocess.run(
                ["docker", "rm", "-f", container], capture_output=True, check=False
            )
            for process in self.processes:
                await process.wait()

    async def run() -> None:
        sandbox = DockerSandbox()
        async with ShellBinding(sandbox, timeout_ms=10000) as binding:
            result = await binding.run_shell_command(
                ShellRequest(
                    command="test -f /.dockerenv && pwd && printf container-only",
                )
            )
            assert result.execution.stdout == "/work\ncontainer-only"
            assert result.execution.exit_code == 0
            impatient = ShellBinding(sandbox, timeout_ms=100)
            result = await impatient.run_shell_command(
                ShellRequest(command="sleep 100")
            )
            assert result.execution.status is ShellStatus.TIMEOUT

    try:
        asyncio.run(run())
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container], capture_output=True, check=False
        )
    status = subprocess.run(
        ["docker", "inspect", container], capture_output=True, check=False
    )
    assert status.returncode != 0


def test_backend_failure_closes_without_fallback() -> None:
    class BrokenSandbox(Sandbox):
        async def execute(
            self, request: ShellRequest, timeout_ms: int
        ) -> ShellExecution:
            raise RuntimeError("sandbox unavailable")

    sandbox = BrokenSandbox(ShellExecution(exit_code=0))
    with pytest.raises(RuntimeError, match="sandbox unavailable"):
        asyncio.run(
            ShellBinding(sandbox, timeout_ms=1000).run_shell_command(
                ShellRequest(command="must-not-run-on-host"),
            )
        )
    assert sandbox.closed
