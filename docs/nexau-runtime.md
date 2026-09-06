# P08b in-container NexAU bridge

`run_in_container(RuntimeRequest, SecretStr)` requires Docker UID 10001 and
no-new-privileges, validates the installed pins and approved baseline/lock hash,
and runs the fixed Azure deployment with the rendered P06 prompt and P07 shell.
There is no host execution or ambient credential/model fallback.

The request declares context/output limits, iterations, total token/time/spend
allowances and shell deadline. Worst-case invocation token reservations must fit
the dispatch token cap. Campaign spend admission/reservations remain controller
policy; this module does not claim live pricing or paid endpoint verification.

Compatibility: single-attempt NexAU calls, explicit zero-retry OpenAI client,
non-streaming Responses API, declared limits, and sequential shell calls on one
asyncio loop. NexAU reserves its final iteration as a stop boundary; a typed
`RuntimeStopped` carries its enum reason. Native traces, tool receipts and typed
runtime exit data are retained under `/logs/agent` for the observability adapter.

P08c launches the entry point using `python -I -m syndicate.nexau_runtime` and
stops the entire trial UID before Harbor verification. That outer isolation is
required even though the P07 backend already reaps its own process groups.
