"""Controller-side NexAU bridge; every tool runs in the supplied E2B sandbox."""

import asyncio
from copy import copy
from dataclasses import dataclass
from pathlib import Path

from e2b import AsyncSandbox
from httpx import URL
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

from syndicate.adapters.e2b_shell import E2BShell
from syndicate.models.baseline import bind_harness
from syndicate.models.runtime import (
    RoleDispatchReceipt,
    RoleDispatchRequest,
    RuntimeRequest,
    installed_runtime,
)
from syndicate.models.shell import ShellBinding, ShellRequest
from syndicate.observability.tracing import neatlogs, wrap_provider


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


async def dispatch_role(
    request: RoleDispatchRequest,
    tools: tuple[Tool, ...],
    client: OpenAI,
    *,
    accept_incomplete: bool = False,
) -> RoleDispatchReceipt:
    """Run one bounded role using an admitted provider and caller-owned tools."""
    if client.max_retries != request.max_retries:
        raise ValueError("Role dispatch requires an OpenAI client with zero retries")
    approved = URL(request.model.endpoint)
    approved = approved.copy_with(path=approved.path.removesuffix("/") + "/")
    if client.base_url != approved:
        raise ValueError("Role dispatch client endpoint differs from approved model")
    completion = _Completion()
    configured_tools = [copy(tool) for tool in tools]
    for tool in configured_tools:
        tool.disable_parallel = True
    agent = Agent(
        config=AgentConfig(
            name=request.role.value,
            system_prompt=request.prompt,
            system_prompt_type="string",
            max_iterations=request.max_iterations,
            max_context_tokens=request.max_context_tokens,
            # NexAU uses range(retry_attempts): one total provider attempt.
            retry_attempts=1,
            tools=configured_tools,
            middlewares=[completion],
            llm_config=LLMConfig(
                model=request.model.deployment,
                base_url=request.model.endpoint,
                api_key=client.api_key,
                api_type="openai_responses",
                max_tokens=request.max_output_tokens,
                stream=False,
                timeout=request.budget.max_seconds,
                max_retries=0,
            ),
        )
    )
    try:
        with neatlogs.trace(f"dispatch-{request.role.value}", kind="WORKFLOW") as span:
            span.set_attribute("input.value", request.prompt)
            client = wrap_provider(client)
            async with asyncio.timeout(request.budget.max_seconds):
                result = await agent.run_async(
                    message=request.prompt,
                    custom_llm_client_provider=lambda _: client,
                )
            allowed = {
                AgentStopReason.SUCCESS,
                AgentStopReason.NO_MORE_TOOL_CALLS,
            }
            if accept_incomplete:
                allowed.add(AgentStopReason.MAX_ITERATIONS_REACHED)
                allowed.add(AgentStopReason.STOP_TOOL_TRIGGERED)
                allowed.add(AgentStopReason.CONTEXT_TOKEN_LIMIT)
            if completion.reason not in allowed:
                raise RuntimeStopped(completion.reason)
            if not isinstance(result, str):
                raise TypeError("Unexpected NexAU response shape")
            span.set_attribute("output.value", result)
            return RoleDispatchReceipt(
                final_text=result,
                usage_ref=request.usage_ref,
                stop_reason=completion.reason,
            )
    finally:
        agent.sync_cleanup()


async def run_on_controller(
    request: RuntimeRequest,
    key: SecretStr,
    sandbox: AsyncSandbox,
    *,
    harness_dir: Path,
    framework_lock: Path,
    trace_events: list[dict[str, object]] | None = None,
) -> str:
    with neatlogs.trace("solve-benchmark-task", kind="WORKFLOW") as span:
        span.set_attribute("input.value", request.instruction)
        installed_runtime()
        baseline = bind_harness(
            harness_dir,
            framework_lock,
            request.baseline.model,
            request.baseline.prompt_variables,
            request.baseline.prompt_suffix,
        )
        if baseline.identity_hash != request.baseline.identity_hash:
            raise ValueError("Runtime baseline differs from approved declaration")
        if not key.get_secret_value().strip():
            raise ValueError("Explicit API credential required")
        async with ShellBinding(
            E2BShell(sandbox, request.baseline.prompt_variables.working_directory),
            timeout_ms=request.shell_timeout_ms,
        ) as shell:
            result = await _run(request, key, shell, harness_dir, trace_events)
        span.set_attribute("output.value", result)
        return result


async def _run(
    request: RuntimeRequest,
    key: SecretStr,
    shell: ShellBinding,
    harness_dir: Path,
    trace_events: list[dict[str, object]] | None = None,
) -> str:
    loop = asyncio.get_running_loop()
    completion = _Completion()

    async def invoke(value: ShellRequest) -> _ToolReply:
        with neatlogs.trace("run-shell-command", kind="TOOL") as span:
            span.set_attribute("tool.name", "run_shell_command")
            span.set_attribute("input.value", value.model_dump_json())
            result = await shell.run_shell_command(value)
            span.set_attribute("output.value", result.model_dump_json())
            if trace_events is not None:
                trace_events.append(
                    {
                        "kind": "shell",
                        "command": value.command,
                        "stdout": result.content,
                    }
                )
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
        client = wrap_provider(client)
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
