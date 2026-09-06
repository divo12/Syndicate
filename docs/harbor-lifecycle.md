# Harbor lifecycle on the controller

`HarborAgent(sandbox, harness_dir=..., framework_lock=...).run(request, key)`
checks that verifier/solution paths (including dangling symlinks) are absent,
then delegates to `run_on_controller`. NexAU and model credentials remain on
the controller; only shell commands enter the supplied E2B task VM.

The existing controller runner owns shell cancellation and whole-UID cleanup.
It returns only after E2BShell verifies that no UID-10001 process remains. Only
then does HarborAgent issue a typed CleanupReceipt. Failed probes, execution,
admission, transport, or cleanup raise; they never produce a successful receipt.
There is no separate process launcher, pgrep interpretation, or cleanup loop here.

The caller owns VM teardown on every terminal path, including failed preflight,
and may start verification only after receiving the successful cleanup proof.
Task templates must provide the dedicated UID/GID and approved workspace.
