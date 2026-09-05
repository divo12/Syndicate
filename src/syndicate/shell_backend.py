"""In-container bash execution. The controller must separately stop the trial UID."""

import asyncio
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from syndicate.shell import ShellExecution, ShellRequest, ShellStatus


class _Capture:
    def __init__(self) -> None:
        self.fd, writer = os.pipe()
        os.set_blocking(self.fd, False)
        self.writer = os.fdopen(writer, "wb")
        self.data = bytearray()
        self.limited = False
        self.eof = False

    def drain(self, limit: int) -> None:
        while not self.limited:
            try:
                chunk = os.read(self.fd, 65536)
            except BlockingIOError:
                return
            if not chunk:
                self.eof = True
                return
            remaining = limit - len(self.data)
            self.data.extend(chunk[:remaining])
            self.limited = len(chunk) > remaining

    def close(self) -> None:
        self.writer.close()
        os.close(self.fd)
        self.data.clear()


@dataclass
class _Job:
    process: asyncio.subprocess.Process
    stdout: _Capture
    stderr: _Capture
    deadline: asyncio.Task[None] | None = None
    timed_out: bool = False


class ContainerShell:
    """Run only as the dedicated nonroot execution identity inside the task container.

    This is not the verifier isolation boundary: the outer adapter must stop and
    confirm the entire trial UID, including escaped setsid descendants, afterward.
    """

    def __init__(self, work_dir: Path, *, capture_limit_bytes: int = 8_000_000) -> None:
        if not Path("/.dockerenv").is_file() or os.getuid() == 0:
            raise RuntimeError("Shell backend requires an unprivileged container user")
        if type(capture_limit_bytes) is not int or capture_limit_bytes <= 0:
            raise ValueError("Positive raw capture limit required")
        self.work_dir = work_dir.resolve(strict=True)
        if not self.work_dir.is_dir():
            raise ValueError("Workspace must be a directory")
        self.work_fd = os.open(self.work_dir, os.O_RDONLY | os.O_DIRECTORY)
        self.capture_limit_bytes = capture_limit_bytes
        self.jobs: list[_Job] = []
        self.closed = False

    def _cwd(self, requested: str | None) -> Path:
        cwd = (self.work_dir / (requested or ".")).resolve(strict=True)
        if not cwd.is_relative_to(self.work_dir) or not cwd.is_dir():
            raise ValueError("Shell directory must be inside workspace")
        return cwd

    @contextmanager
    def _directory_fd(self, cwd: Path) -> Iterator[int]:
        descriptor = os.dup(self.work_fd)
        try:
            for part in cwd.relative_to(self.work_dir).parts:
                child = os.open(
                    part,
                    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            yield descriptor
        finally:
            os.close(descriptor)

    async def _spawn(self, request: ShellRequest, cwd: Path) -> _Job:
        stdout = _Capture()
        try:
            stderr = _Capture()
        except BaseException:
            stdout.close()
            raise
        try:
            with (
                self._directory_fd(cwd) as cwd_fd,
                stdout.writer as out,
                stderr.writer as err,
            ):
                process = await asyncio.create_subprocess_exec(
                    "/bin/bash",
                    "-c",
                    request.command,
                    cwd=f"/proc/self/fd/{cwd_fd}",
                    pass_fds=(cwd_fd,),
                    stdout=out,
                    stderr=err,
                    start_new_session=True,
                )
        except BaseException:
            stdout.close()
            stderr.close()
            raise
        job = _Job(process, stdout, stderr)
        self.jobs.append(job)
        return job

    async def _stop(self, job: _Job) -> None:
        try:
            os.killpg(job.process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await job.process.wait()

    async def _expire(self, job: _Job, timeout_ms: int) -> None:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        # ponytail: 10ms polling; use add_reader if interactive streaming is needed.
        while job.process.returncode is None:
            job.stdout.drain(self.capture_limit_bytes)
            job.stderr.drain(self.capture_limit_bytes)
            job.timed_out = asyncio.get_running_loop().time() >= deadline
            if job.timed_out or job.stdout.limited or job.stderr.limited:
                break
            await asyncio.sleep(0.01)
        await self._stop(job)

    def _receipt(self, job: _Job, background: bool) -> ShellExecution:
        job.stdout.drain(self.capture_limit_bytes)
        job.stderr.drain(self.capture_limit_bytes)
        limited = job.stdout.limited or job.stderr.limited
        status = ShellStatus.TIMEOUT if job.timed_out else ShellStatus.EXITED
        return ShellExecution(
            status=ShellStatus.BACKGROUND if background else status,
            stdout=job.stdout.data.decode(errors="replace"),
            stderr=job.stderr.data.decode(errors="replace"),
            exit_code=None if background else job.process.returncode,
            background_pid=job.process.pid if background else None,
            error="Raw capture limit reached" if limited else None,
            capture_complete=not (background or limited)
            and job.stdout.eof
            and job.stderr.eof,
        )

    async def execute(self, request: ShellRequest, timeout_ms: int) -> ShellExecution:
        if self.closed:
            raise RuntimeError("Shell backend is closed")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("Positive timeout required")
        spawn = asyncio.create_task(self._spawn(request, self._cwd(request.dir_path)))
        try:
            job = await asyncio.shield(spawn)
            job.deadline = asyncio.create_task(self._expire(job, timeout_ms))
            if request.is_background:
                return self._receipt(job, background=True)
            await job.deadline
            receipt = self._receipt(job, background=False)
            job.stdout.close()
            job.stderr.close()
            self.jobs.remove(job)
            return receipt
        except BaseException:
            # A cancelled spawn can still create a process: settle it before cleanup.
            await asyncio.gather(spawn, return_exceptions=True)
            await self.close()
            raise

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.work_fd)
        jobs, self.jobs = self.jobs, []
        try:
            for job in jobs:
                if job.deadline is not None:
                    job.deadline.cancel()
                    await asyncio.gather(job.deadline, return_exceptions=True)
                await self._stop(job)
        finally:
            for job in jobs:
                job.stdout.close()
                job.stderr.close()
