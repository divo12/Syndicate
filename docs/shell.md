# Sandbox shell binding — M1-T1-S2 / P07

Use `async with ShellBinding(sandbox, timeout_ms=...)` and await
`run_shell_command(ShellRequest(...))`. There is no implicit sandbox or host-shell
fallback. P07b's `ContainerShell` implements `SandboxShell.execute`/`close` and
owns confined cwd, bash process groups, deadlines and transient RAM capture.
Worker8 owns that backend. Worker9/P08 owns runtime dependencies/install, task
container identity and whole-trial UID stop confirmation, including descendants
that escape groups, before P09 injects the verifier. Do not create a second backend.
The binding bounds response time and invokes cleanup on timeout, cancellation,
errors and context exit. A nonresponsive backend can still defeat cleanup;
production backend integration must prove these obligations before H0 is sealed.

`ShellResult.execution` carries transient raw stdout/stderr, status, exit code
and PID separately from model-visible content. P07b supplies no capture paths;
anonymous buffers are released, with no local durable trace copy or fallback. Seed truncation preserves
the 4M threshold, last 1000 lines/1000-column clipping, and single-line 4000 tail.
Background capture is explicitly incomplete. Controller response timeout cannot
claim recovered output; it records incomplete capture.

Compatibility deviations from pinned AHE shell: typed receipts replace dicts;
timeout is mandatory and operator-owned; no missing-PID success fallback;
background text omits the unavailable BackgroundTaskManage tool; unknown exit
codes are not formatted as real exit codes. No extra model tools are introduced.

`SYNDICATE_DOCKER_TEST=1 .venv/bin/python -m pytest tests/test_shell.py` runs a
synthetic no-model container smoke test with an explicitly supplied test backend.
It checks binding dispatch and timeout cleanup. P07b's separate backend tests
cover actual process groups and cwd checks. Harbor lifecycle, whole-UID shutdown
and protected verifier mounts remain P08/P09 integration obligations.
