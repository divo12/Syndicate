# Sandbox shell binding — M1-T1-S2 / P07

Use `async with ShellBinding(sandbox, timeout_ms=...)` and await
`run_shell_command(ShellRequest(...))`. There is no implicit sandbox or host-shell
fallback. `SandboxShell.execute`/`close` are the typed backend boundary for P08.
The backend owns container identity, real directory/symlink confinement, bash
process groups, positive execution deadlines and idempotent background reaping.
The binding bounds response time and invokes cleanup on timeout, cancellation,
errors and context exit. A nonresponsive backend can still defeat cleanup;
production backend integration must prove these obligations before H0 is sealed.

`ShellResult.execution` retains raw stdout/stderr, status, exit code, PID and
capture paths separately from model-visible content. Seed truncation preserves
the 4M threshold, last 1000 lines/1000-column clipping, and single-line 4000 tail.
Background capture is explicitly incomplete. Controller response timeout cannot
claim recovered output; it records incomplete capture.

Compatibility deviations from pinned AHE shell: typed receipts replace dicts;
timeout is mandatory and operator-owned; no missing-PID success fallback;
background text omits the unavailable BackgroundTaskManage tool; unknown exit
codes are not formatted as real exit codes. No extra model tools are introduced.

`SYNDICATE_DOCKER_TEST=1 .venv/bin/python -m pytest tests/test_shell.py` runs a
synthetic no-model container smoke test with an explicitly supplied test backend.
It checks container-only execution and timeout cleanup, not Harbor lifecycle,
background groups or protected verifier mounts (P08/P09 integration obligations).
