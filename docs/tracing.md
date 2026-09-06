# Neatlogs tracing

The controller's public `run_on_controller` API is the model-calling entry point.
It produces one `solve-benchmark-task` workflow per invocation. Its existing
OpenAI Responses client is wrapped once, and each remote shell invocation is a
tool span. Offline preflight and health-only operations are not traced.

Load credentials before importing the controller runtime. For a caller launched
from this repository, use `uv run --env-file .env python your_controller.py`, or
export `NEATLOGS_API_KEY` in that process's environment. Never commit the key.
The library does not search sibling repositories or load arbitrary dotenv files.

SDK initialization occurs once when the controller imports its tracing module.
It uses the project selected by the environment key. The SDK's built-in threading
instrumentation propagates context through NexAU's worker pool; no second provider
instrumentor is installed. The existing thread-to-async shell bridge is retained.

Normal one-shot process exit explicitly flushes and shuts down the SDK. A server
embedding this API should call `shutdown_tracing()` from its existing shutdown
hook, never after individual requests. This integration leaves server signal
ownership unchanged. Each concurrent invocation has its own workflow root.

Only instruction/result and tool input/output are explicitly captured at the
workflow/tool boundaries. Credential-bearing runtime arguments and sandbox client
objects are not serialized into spans. Wrapped LLM calls capture provider I/O
according to Neatlogs' documented OpenAI integration.

The SDK version is resolved from the latest stable release when refreshing the
lock. `python -m neatlogs doctor --local --json` checks its offline envelope.
No application or authenticated probe was run during setup; hosted trace delivery
must be confirmed when the operator next runs the controller.

Remote readback and run-link receipt contracts belong to PR25. They are not part
of this tracing-only layer and are not claimed to validate these workflow spans.
