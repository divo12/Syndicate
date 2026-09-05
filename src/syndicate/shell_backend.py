"""In-container bash execution. The controller must separately stop the trial UID."""

import asyncio
import os
import signal
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from syndicate.shell import ShellExecution, ShellRequest, ShellStatus

# Limit inherited output files before exec; no preexec_fn in the async runtime.
_BOOTSTRAP = """import os, resource, sys
limit = int(sys.argv[1])
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
os.execv('/bin/bash', ['bash', '-c', sys.argv[2]])
"""


@dataclass
class _Job:
    process: asyncio.subprocess.Process
    stdout: Path
    stderr: Path
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
        capture_dir = Path(tempfile.mkdtemp(prefix="syndicate-shell-"))
        stdout, stderr = capture_dir / "stdout", capture_dir / "stderr"
        with (
            self._directory_fd(cwd) as cwd_fd,
            stdout.open("xb") as out,
            stderr.open("xb") as err,
        ):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                _BOOTSTRAP,
                str(self.capture_limit_bytes),
                request.command,
                cwd=f"/proc/self/fd/{cwd_fd}",
                pass_fds=(cwd_fd,),
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
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
        await asyncio.sleep(timeout_ms / 1000)
        job.timed_out = job.process.returncode is None
        await self._stop(job)

    def _receipt(self, job: _Job, background: bool) -> ShellExecution:
        with job.stdout.open("rb") as out, job.stderr.open("rb") as err:
            stdout = out.read(self.capture_limit_bytes)
            stderr = err.read(self.capture_limit_bytes)
        limited = max(len(stdout), len(stderr)) >= self.capture_limit_bytes
        status = ShellStatus.TIMEOUT if job.timed_out else ShellStatus.EXITED
        return ShellExecution(
            status=ShellStatus.BACKGROUND if background else status,
            stdout=stdout.decode(errors="replace"),
            stderr=stderr.decode(errors="replace"),
            exit_code=None if background else job.process.returncode,
            background_pid=job.process.pid if background else None,
            error="Raw capture limit reached" if limited else None,
            capture_complete=not (background or limited),
            stdout_file=str(job.stdout),
            stderr_file=str(job.stderr),
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
            await job.process.wait()
            await self._stop(job)
            job.deadline.cancel()
            await asyncio.gather(job.deadline, return_exceptions=True)
            return self._receipt(job, background=False)
        except BaseException:
            # A cancelled spawn can still create a process: settle it before cleanup.
            await asyncio.gather(spawn, return_exceptions=True)
            await self.close()
            raise

    async def close(self) -> None:
        if not self.closed:
            self.closed = True
            os.close(self.work_fd)
        for job in self.jobs:
            if job.deadline is not None:
                job.deadline.cancel()
                await asyncio.gather(job.deadline, return_exceptions=True)
            await self._stop(job)
