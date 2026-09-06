"""Controller-side NexAU bridge; every tool runs in the supplied E2B sandbox."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from e2b import AsyncSandbox
from nexau import Agent, AgentConfig
from nexau.archs.llm.llm_config import LLMConfig
from nexau.archs.main_sub.execution.hooks import (
    AfterAgentHookInput,
    HookResult,
    Middleware,
)
from nexau.archs.main_sub.execution.stop_reason import AgentStopReason
from nexau.archs.tool import Tool
from openai import OpenAI
from pydantic import SecretStr

from syndicate.baseline import prepare_baseline
from syndicate.observability.tracing import neatlogs
from syndicate.runtime_contracts import RuntimeRequest, installed_runtime
from syndicate.shell import ShellBinding, ShellRequest
from syndicate.shell_backend import E2BShell


class RuntimeStopped(RuntimeError):
    def __init__(self, reason: AgentStopReason | None) -> None:
        self.reason = reason
        super().__init__("NexAU stopped without completion")


class _Completion(Middleware):
    reason: AgentStopReason | None = None

    def after_agent(self, hook_input: AfterAgentHookInput) -> HookResult:
        self.reason = hook_input.stop_reason
        return HookResult.no_changes()


@dataclass(frozen=True, slots=True)
class _ToolReply:
    content: str
    returnDisplay: str


async def run_on_controller(
    request: RuntimeRequest,
    key: SecretStr,
    sandbox: AsyncSandbox,
    *,
    harness_dir: Path,
    framework_lock: Path,
) -> str:
    with neatlogs.trace("solve-benchmark-task", kind="WORKFLOW") as span:
        span.set_attribute("input.value", request.instruction)
        installed_runtime()
        baseline = prepare_baseline(
            harness_dir,
            framework_lock,
            request.baseline.model,
            request.baseline.prompt_variables,
        )
        if baseline.identity_hash != request.baseline.identity_hash:
            raise ValueError("Runtime baseline differs from approved declaration")
        if not key.get_secret_value().strip():
            raise ValueError("Explicit API credential required")
        async with ShellBinding(
            E2BShell(sandbox, request.baseline.prompt_variables.working_directory),
            timeout_ms=request.shell_timeout_ms,
        ) as shell:
            result = await _run(request, key, shell, harness_dir)
        span.set_attribute("output.value", result)
        return result


async def _run(
    request: RuntimeRequest, key: SecretStr, shell: ShellBinding, harness_dir: Path
) -> str:
    loop = asyncio.get_running_loop()
    completion = _Completion()

    async def invoke(value: ShellRequest) -> _ToolReply:
        with neatlogs.trace("run-shell-command", kind="TOOL") as span:
            span.set_attribute("tool.name", "run_shell_command")
            span.set_attribute("input.value", value.model_dump_json())
            result = await shell.run_shell_command(value)
            span.set_attribute("output.value", result.model_dump_json())
            return _ToolReply(result.content, result.return_display)

    def tool(
        command: str,
        description: str | None = None,
        is_background: bool = False,
        dir_path: str | None = None,
    ) -> _ToolReply:
        value = ShellRequest(
            command=command,
            description=description,
            is_background=is_background,
            dir_path=dir_path,
        )
        return asyncio.run_coroutine_threadsafe(invoke(value), loop).result()

    configured_tool = Tool.from_yaml(
        str(harness_dir / "tool_descriptions/run_shell_command.tool.yaml"), binding=tool
    )
    configured_tool.disable_parallel = True
    model = request.baseline.model
    config = AgentConfig(
        name="Agent A",
        system_prompt=request.baseline.rendered_prompt,
        system_prompt_type="string",
        max_iterations=request.max_iterations,
        max_context_tokens=request.max_context_tokens,
        retry_attempts=1,
        tools=[configured_tool],
        middlewares=[completion],
        llm_config=LLMConfig(
            model=model.deployment,
            base_url=model.endpoint,
            api_key=key.get_secret_value(),
            api_type="openai_responses",
            max_tokens=request.max_output_tokens,
            stream=False,
            timeout=request.budget.max_seconds,
            max_retries=0,
        ),
    )
    agent = Agent(config=config)
    # NexAU 0.3.9 drops max_retries=0 from client kwargs; supply the explicit client.
    with OpenAI(
        api_key=key.get_secret_value(),
        base_url=model.endpoint,
        max_retries=0,
        timeout=request.budget.max_seconds,
    ) as client:
        client = neatlogs.wrap(client)
        try:
            async with asyncio.timeout(request.budget.max_seconds):
                result = await agent.run_async(
                    message=request.instruction,
                    custom_llm_client_provider=lambda _: client,
                )
            if completion.reason not in (
                AgentStopReason.SUCCESS,
                AgentStopReason.NO_MORE_TOOL_CALLS,
            ):
                raise RuntimeStopped(completion.reason)
            if not isinstance(result, str):
                raise TypeError("Unexpected NexAU response shape")
            return result
        finally:
            agent.sync_cleanup()
