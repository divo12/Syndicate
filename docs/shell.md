# Sandbox shell binding — M1-T1-S2 / P07

Use `async with ShellBinding(sandbox, timeout_ms=...)` and await
`run_shell_command(ShellRequest(...))`. There is no implicit sandbox or host-shell
fallback. P07b's controller-side `E2BShell` implements `SandboxShell.execute`/`close`
using an explicitly supplied E2B task VM. It owns confined cwd, command deadlines,
transient capture, and whole-UID cleanup. Harbor retains VM creation/deletion and
must wait for successful cleanup before injecting verifier inputs.
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
cover the E2B adapter without external services; `scripts/e2b_smoke.py` exercises
real remote execution. See [E2B backend](shell-backend.md) for setup and ownership.
