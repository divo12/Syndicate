"""AHE shell presentation bound exclusively to a caller-supplied task sandbox."""

import asyncio
import time
from enum import StrEnum
from types import TracebackType
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ShellRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    command: str = Field(pattern=r"\S")
    description: str | None = None
    is_background: bool = False
    dir_path: str | None = None


class ShellStatus(StrEnum):
    EXITED = "exited"
    TIMEOUT = "timeout"
    BACKGROUND = "background"
    ERROR = "error"


class ShellExecution(BaseModel):
    """Unmodified sandbox evidence; capture paths refer only to the task sandbox."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    status: ShellStatus = ShellStatus.EXITED
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None
    background_pid: int | None = Field(default=None, gt=0)
    error: str | None = None
    capture_complete: bool = True
    stdout_file: str | None = None
    stderr_file: str | None = None

    @model_validator(mode="after")
    def coherent_status(self) -> Self:
        if self.status is ShellStatus.BACKGROUND:
            if (
                self.background_pid is None
                or self.capture_complete
                or self.exit_code is not None
            ):
                raise ValueError(
                    "Background execution requires PID and incomplete capture"
                )
        if self.status is ShellStatus.EXITED and self.exit_code is None:
            raise ValueError("Exited execution requires an exit code")
        return self


class SandboxShell(Protocol):
    """Backend owns confined cwd resolution, process groups and retained raw output.

    execute must run bash only in the owned task sandbox, enforce timeout_ms,
    respect cancellation, and reject nonexistent/out-of-workspace directories.
    close must idempotently reap all owned processes, including background children.
    """

    async def execute(
        self, request: ShellRequest, timeout_ms: int
    ) -> ShellExecution: ...

    async def close(self) -> None: ...


class ShellResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    execution: ShellExecution
    content: str
    return_display: str
    duration_ms: int = Field(ge=0)


def truncate_output(content: str) -> str:
    """Preserve the pinned seed's 4M threshold, tail and long-line formatting."""
    if len(content) <= 4_000_000:
        return content
    lines = content.split("\n")
    if len(lines) == 1:
        return (
            "Output too large. Showing the last 4,000 characters of the output.\n..."
            + content[-4000:]
        )
    tail = [
        line[:1000] + "... [LINE WIDTH TRUNCATED]" if len(line) > 1000 else line
        for line in lines[-1000:]
    ]
    return (
        f"Output too large. Showing the last {len(tail)} of {len(lines)} lines.\n...\n"
        + "\n".join(tail)
    )


def _display(raw: ShellExecution, output: str, timeout_ms: int) -> str:
    if raw.status is ShellStatus.BACKGROUND:
        return f"Background task started (pid: {raw.background_pid})"
    if output.strip():
        return output
    if raw.status is ShellStatus.TIMEOUT:
        return f"Command timed out after {timeout_ms / 60000:.1f} minutes."
    if raw.error:
        return f"Command failed: {raw.error}"
    if raw.exit_code not in (None, 0):
        return f"Command exited with code: {raw.exit_code}"
    return "(empty)"


def _render(raw: ShellExecution, timeout_ms: int, duration_ms: int) -> ShellResult:
    output = truncate_output(
        "\n".join(part for part in (raw.stdout, raw.stderr) if part)
    )
    if raw.status is ShellStatus.BACKGROUND:
        content = f"Background task started (pid: {raw.background_pid})."
    elif raw.status is ShellStatus.TIMEOUT:
        content = f"Timeout: command timed out after {timeout_ms / 60000:.1f} minutes."
    else:
        content = f"Output: {output or '(empty)'}"
    if raw.error:
        content += f"\nError: {raw.error}"
    if raw.exit_code not in (None, 0):
        content += f"\nExit Code: {raw.exit_code}"
    return ShellResult(
        execution=raw,
        content=content,
        return_display=_display(raw, output, timeout_ms),
        duration_ms=duration_ms,
    )


class ShellBinding:
    """Use as an async context manager so every trial exit reaps background work."""

    def __init__(self, sandbox: SandboxShell, *, timeout_ms: int) -> None:
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("A positive shell timeout is required")
        self.sandbox = sandbox
        self.timeout_ms = timeout_ms

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.sandbox.close()

    async def run_shell_command(self, request: ShellRequest) -> ShellResult:
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(
                self.sandbox.execute(request, self.timeout_ms),
                self.timeout_ms / 1000,
            )
        except TimeoutError:
            await self.sandbox.close()
            raw = ShellExecution(
                status=ShellStatus.TIMEOUT,
                exit_code=None,
                capture_complete=False,
                error="Sandbox response deadline expired; capture is incomplete",
            )
        except BaseException:
            await self.sandbox.close()
            raise
        return _render(raw, self.timeout_ms, int((time.monotonic() - started) * 1000))
