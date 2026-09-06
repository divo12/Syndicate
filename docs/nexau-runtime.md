# Controller-side NexAU bridge

`run_on_controller(request, key, sandbox, harness_dir=..., framework_lock=...)`
validates installed pins and the approved baseline/lock hash, then runs NexAU on
the controller with the fixed deployment and approved prompt. The caller supplies
the Harbor-created E2B sandbox. Every shell command goes through `E2BShell` into
that sandbox; there is no local shell or ambient credential/model fallback.

The request declares context/output limits, iterations, total token/time/spend
allowances and shell deadline. Worst-case invocation token reservations must fit
the dispatch token cap. Campaign spend admission/reservations remain controller
policy; this module does not claim live pricing or paid endpoint verification.

Compatibility: single-attempt NexAU calls, explicit zero-retry OpenAI client,
non-streaming Responses API, declared limits, and sequential shell calls on one
asyncio loop. NexAU reserves its final iteration as a stop boundary; a typed
`RuntimeStopped` carries its enum reason. The bridge returns final text or raises
the execution failure; it writes no host traces, receipts, or result files.

Exiting the shell binding stops the owned sandbox UID before returning control
to verification, preserving the sandbox filesystem. The caller owns sandbox
creation and final teardown; model credentials remain on the controller.
