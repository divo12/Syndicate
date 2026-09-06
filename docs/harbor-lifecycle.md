# Harbor lifecycle

`SyndicateNexAUAgent` uses the sandbox already owned by Harbor's E2B environment.
NexAU, model credentials, approved request, and seed harness stay on the controller.
Only shell commands cross into E2B, under dedicated UID/GID `10001`.

The task template must provision that user, the approved writable workspace, and
`bash`, `setpriv`, `setsid`, `timeout`, `pkill`, and `pgrep`. Setup checks these
prerequisites; it does not change ownership or permissions on task paths.
Controller callers can supply `harness_dir` and `framework_lock`; defaults refer
to this checkout's seed harness and `requirements.lock`.

Before dispatch, the lifecycle checks that hidden verifier paths are absent.
`run_on_controller()` owns the E2B shell and always attempts whole-UID cleanup.
A cleanup receipt is returned only after runtime success and verified process
termination. Execution, inspection, or cleanup failures propagate to Harbor,
without issuing a successful cleanup receipt. Harbor retains VM ownership and
sole authority to invoke its original verifier after successful handoff.

The adapter isolates access to Harbor 0.22.0's private `_sandbox` field in one
checked accessor because that pinned release provides no public accessor.

Neatlogs instrumentation remains on the controller. This adapter uploads no
credentials, runtime files, or harness payloads into the benchmark environment.

Cleanup proof issuance is internal to the bound adapter after its awaited
`HarborAgent.run()` return. The controller remembers only digests it issued in
this process; the contained nofollow receipt path binds operation, attempt, run,
task, environment context, agent import/name, UID, and aware issuance time. A
receipt from another process, context, path, adapter, or timing interval fails
closed.
