"""Controller-side shell execution in an exclusively owned E2B task sandbox."""

import asyncio
import shlex
from dataclasses import dataclass, field

from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException
from e2b.sandbox_async.commands.command_handle import AsyncCommandHandle

from syndicate.shell import ShellExecution, ShellRequest, ShellStatus


class CaptureLimitError(RuntimeError):
    """Stop the SDK stream when its per-stream output allowance is exhausted."""


@dataclass
class _Capture:
    limit: int
    data: bytearray = field(default_factory=bytearray)

    def append(self, text: str) -> None:
        chunk = text.encode()
        remaining = self.limit - len(self.data)
        self.data.extend(chunk[:remaining])
        if len(chunk) > remaining:
            raise CaptureLimitError("Raw capture limit reached")


class E2BShell:
    """Close stops the dedicated task UID; the caller retains VM/verifier ownership."""

    def __init__(
        self,
        sandbox: AsyncSandbox,
        work_dir: str,
        *,
        uid: int = 10001,
        capture_limit_bytes: int = 8_000_000,
    ) -> None:
        if not work_dir.startswith("/") or "\x00" in work_dir:
            raise ValueError("Absolute sandbox workspace required")
        if type(uid) is not int or uid <= 0:
            raise ValueError("Dedicated nonroot UID required")
        if type(capture_limit_bytes) is not int or capture_limit_bytes <= 0:
            raise ValueError("Positive capture limit required")
        self.sandbox = sandbox
        self.work_dir = work_dir
        self.uid = uid
        self.capture_limit_bytes = capture_limit_bytes
        self.pending: set[asyncio.Task[ShellExecution]] = set()
        self.spawns: set[asyncio.Task[AsyncCommandHandle]] = set()
        self.close_lock = asyncio.Lock()
        self.closed = False
        self.cleanup_complete = False

    def _command(self, request: ShellRequest, timeout_ms: int) -> str:
        # The supervisor survives child-group cleanup and retains its exit code.
        script = (
            f'cd -P -- {shlex.quote(self.work_dir)} || exit 125; root="${{PWD%/}}"; '
            f"cd -P -- {shlex.quote(request.dir_path or '.')} || exit 125; "
            'case "$PWD" in "$root"|"$root"/*) ;; *) exit 125;; esac; '
            f'test "$(id -u)" = {self.uid} || exit 125; '
            f"setsid timeout -s KILL {timeout_ms / 1000 + 5} "
            f"/bin/bash --noprofile --norc -c {shlex.quote(request.command)} & "
            'child=$!; wait "$child"; code=$?; '
            'kill -KILL -- "-$child" 2>/dev/null || :; exit "$code"'
        )
        return (
            f"exec setpriv --reuid={self.uid} --regid={self.uid} --clear-groups "
            f"--no-new-privs /bin/bash --noprofile --norc -c {shlex.quote(script)}"
        )

    async def _kill_owned(self) -> None:
        self.closed = True
        await self.sandbox.commands.run(
            f"pkill -KILL -u {self.uid} || test $? = 1",
            user="root",
            timeout=5,
        )

    async def _wait(
        self,
        handle: AsyncCommandHandle,
        stdout: _Capture,
        stderr: _Capture,
        timeout_ms: int,
    ) -> ShellExecution:
        status, code, error = ShellStatus.EXITED, None, None
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                result = await handle.wait()
            code = result.exit_code
        except CommandExitException as exc:
            code = exc.exit_code
        except TimeoutError:
            status, error = ShellStatus.TIMEOUT, "E2B command deadline expired"
        except CaptureLimitError:
            status, error = ShellStatus.ERROR, "Raw capture limit reached"
        except Exception:
            status, error = ShellStatus.ERROR, "E2B command stream failed"
        finally:
            await handle.disconnect()
        if error:
            await self._kill_owned()
        return ShellExecution(
            status=status,
            exit_code=code,
            error=error,
            stdout=stdout.data.decode(errors="replace"),
            stderr=stderr.data.decode(errors="replace"),
            capture_complete=status == ShellStatus.EXITED,
        )

    def _finished(self, task: asyncio.Task[ShellExecution]) -> None:
        self.pending.discard(task)
        # Retrieve background exceptions; close still independently verifies cleanup.
        if not task.cancelled() and task.exception() is not None:
            self.closed = True

    async def execute(self, request: ShellRequest, timeout_ms: int) -> ShellExecution:
        if self.closed:
            raise RuntimeError("E2B shell is closed")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("Positive timeout required")
        stdout, stderr = (
            _Capture(self.capture_limit_bytes),
            _Capture(self.capture_limit_bytes),
        )
        spawn = asyncio.create_task(
            self.sandbox.commands.run(
                self._command(request, timeout_ms),
                background=True,
                user="root",
                timeout=timeout_ms / 1000 + 10,
                request_timeout=10,
                on_stdout=stdout.append,
                on_stderr=stderr.append,
            )
        )
        self.spawns.add(spawn)
        try:
            handle = await asyncio.shield(spawn)
            if self.closed:
                raise RuntimeError("E2B shell closed during command startup")
            waiter = asyncio.create_task(self._wait(handle, stdout, stderr, timeout_ms))
            self.pending.add(waiter)
            waiter.add_done_callback(self._finished)
            if request.is_background:
                return ShellExecution(
                    status=ShellStatus.BACKGROUND,
                    exit_code=None,
                    background_pid=handle.pid,
                    capture_complete=False,
                )
            return await waiter
        except BaseException:
            await asyncio.gather(spawn, return_exceptions=True)
            await self.close()
            raise
        finally:
            self.spawns.discard(spawn)

    async def close(self) -> None:
        self.closed = True
        async with self.close_lock:
            await self._close()

    async def _close(self) -> None:
        if self.cleanup_complete:
            return
        started = await asyncio.gather(*tuple(self.spawns), return_exceptions=True)
        pending = tuple(self.pending)
        for waiter in pending:
            waiter.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for handle in started:
            if isinstance(handle, AsyncCommandHandle):
                await handle.disconnect()
        await self._kill_owned()
        for _ in range(20):
            try:
                await self.sandbox.commands.run(
                    f"pgrep -u {self.uid}",
                    user="root",
                    timeout=5,
                )
            except CommandExitException as exc:
                if exc.exit_code == 1:
                    self.cleanup_complete = True
                    return
                raise RuntimeError("E2B cleanup could not be verified") from None
            await asyncio.sleep(0.05)
        raise RuntimeError("E2B task processes remain; verifier must stay blocked")
