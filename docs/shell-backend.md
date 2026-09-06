# E2B shell backend

`E2BShell(sandbox, work_dir)` implements `SandboxShell` on the controller.
The supplied `e2b.AsyncSandbox` must be an exclusively owned task VM with a
dedicated user/group at UID/GID 10001 and the approved workspace already prepared.
The controller owns creation and deletion; the adapter never creates a second VM
or executes a local subprocess. Model and E2B credentials stay on the controller.

The trusted remote bootstrap immediately uses `setpriv` to drop user/group,
clear supplementary groups, and disable new privileges. Task shells skip login
profiles. Workspace checks happen after entering the requested directory, and
all interpolated task strings are shell-quoted before crossing the root bootstrap.
This confines the initial cwd, not arbitrary task filesystem access: verifier and
solution files must remain absent until cleanup succeeds.

E2B streams output to bounded in-memory captures. Crossing either limit terminates
the task UID and marks output incomplete. The SDK also buffers output internally:
its allocation may exceed our cap by one transport chunk before the callback
stops consumption. No output files or local trace fallback are created.

Each remote command has a server-side deadline in addition to its controller
deadline. A small supervisor kills ordinary child-group members on completion
while preserving the exit code. Background replies are incomplete startup snapshots;
finished monitor tasks and their captures are released immediately.

`close()` kills and verifies the entire dedicated UID, including descendants that
used `setsid`, then leaves the VM intact for verification. A cleanup error must
block the verifier. Timeout, output overflow, or stream failure closes the shell
to further commands. The outer owner must delete the VM even if cleanup fails.

Run offline checks with `python -m pytest tests/test_shell_backend.py`.
For an opt-in real E2B smoke (one VM, 120-second TTL, deleted in `finally`):

```sh
uv run --no-project --python .venv/bin/python --env-file /path/to/private.env \
  python scripts/e2b_smoke.py
```

Only `E2B_API_KEY` is needed for the smoke; it makes no model calls. Production
ITSMBench runs still require compatible, provenance-reviewed E2B task templates.
HDP's modified Harbor fork and prebuilt aliases are not silently substituted for
Syndicate's pinned runtime.
