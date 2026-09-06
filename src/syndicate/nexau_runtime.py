"""NexAU executor entry point; this module never runs tools on the controller host."""

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path

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
from syndicate.model_config import ModelSettings
from syndicate.runtime_contracts import (
    RoleDispatchReceipt,
    RoleDispatchRequest,
    RuntimeRequest,
    installed_runtime,
)
from syndicate.shell import ShellBinding, ShellRequest
from syndicate.shell_backend import ContainerShell

HARNESS = Path("/run/syndicate/harness")


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


def _llm_config(
    model: ModelSettings, api_key: str, max_output_tokens: int, max_seconds: int
) -> LLMConfig:
    return LLMConfig(
        model=model.deployment,
        base_url=model.endpoint,
        api_key=api_key,
        api_type="openai_responses",
        max_tokens=max_output_tokens,
        stream=False,
        timeout=max_seconds,
        max_retries=0,
    )


async def dispatch_role(
    request: RoleDispatchRequest, tools: tuple[Tool, ...], client: OpenAI
) -> RoleDispatchReceipt:
    """Run one bounded product role with caller-supplied tools and client only."""
    if client.max_retries != request.max_retries:
        raise ValueError("Role dispatch requires an OpenAI client with zero retries")
    completion = _Completion()
    for tool in tools:
        tool.disable_parallel = True
    config = AgentConfig(
        name=request.role.value,
        system_prompt=request.prompt,
        system_prompt_type="string",
        max_iterations=request.max_iterations,
        max_context_tokens=request.max_context_tokens,
        # NexAU uses range(retry_attempts), so one means one total provider call.
        retry_attempts=1,
        tools=list(tools),
        middlewares=[completion],
        llm_config=_llm_config(
            request.model,
            client.api_key,
            request.max_output_tokens,
            request.budget.max_seconds,
        ),
    )
    agent = Agent(config=config)
    try:
        async with asyncio.timeout(request.budget.max_seconds):
            result = await agent.run_async(
                message=request.prompt,
                custom_llm_client_provider=lambda _: client,
            )
        if completion.reason not in (
            AgentStopReason.SUCCESS,
            AgentStopReason.NO_MORE_TOOL_CALLS,
        ):
            raise RuntimeStopped(completion.reason)
        if not isinstance(result, str):
            raise TypeError("Unexpected NexAU response shape")
        return RoleDispatchReceipt(
            final_text=result,
            usage_ref=request.usage_ref,
            stop_reason=completion.reason,
        )
    finally:
        agent.sync_cleanup()


async def run_in_container(request: RuntimeRequest, key: SecretStr) -> str:
    if os.getuid() != 10001 or not Path("/.dockerenv").is_file():
        raise RuntimeError("NexAU requires the dedicated task container user")
    if "\nNoNewPrivs:\t1" not in Path("/proc/self/status").read_text():
        raise RuntimeError("NexAU requires no-new-privileges")
    installed_runtime()
    baseline = prepare_baseline(
        HARNESS,
        Path("/opt/syndicate/requirements.lock"),
        request.baseline.model,
        request.baseline.prompt_variables,
    )
    if baseline.identity_hash != request.baseline.identity_hash:
        raise ValueError("Runtime baseline differs from approved declaration")
    if not key.get_secret_value().strip():
        raise ValueError("Explicit API credential required")
    async with ShellBinding(
        ContainerShell(Path("/app")), timeout_ms=request.shell_timeout_ms
    ) as shell:
        return await _run(request, key, shell)


async def _run(request: RuntimeRequest, key: SecretStr, shell: ShellBinding) -> str:
    loop = asyncio.get_running_loop()
    completion = _Completion()

    async def invoke(value: ShellRequest) -> _ToolReply:
        result = await shell.run_shell_command(value)
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
        str(HARNESS / "tool_descriptions/run_shell_command.tool.yaml"), binding=tool
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
        llm_config=_llm_config(
            model,
            key.get_secret_value(),
            request.max_output_tokens,
            request.budget.max_seconds,
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


if __name__ == "__main__":
    value = RuntimeRequest.model_validate_json(
        Path("/run/syndicate/request.json").read_bytes()
    )
    credential_path = Path("/run/syndicate/api-key")
    credential = SecretStr(credential_path.read_text())
    credential_path.unlink()
    # NexAU patches asyncio.run; Runner still owns and closes the loop explicitly.
    with asyncio.Runner() as runner:
        runner.run(run_in_container(value, credential))
