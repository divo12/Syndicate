"""Run NexAU on the controller against Harbor's existing E2B task sandbox."""

import asyncio
import shlex
from datetime import UTC, datetime
from logging import Logger
from pathlib import Path, PurePosixPath
from typing import override

from e2b import AsyncSandbox
from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.environments.e2b import E2BEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import MCPServerConfig
from pydantic import SecretStr

from syndicate.adapters.harbor_agent import CleanupReceipt, HarborAgent
from syndicate.models.runtime import RuntimeRequest
from syndicate.services.stock import ControllerTrialBinding, emit_cleanup_receipt

HARNESS_SOURCE = Path(__file__).parents[3] / "harnesses/seed"
FRAMEWORK_LOCK = Path(__file__).parents[3] / "requirements.lock"


def _sandbox(environment: BaseEnvironment) -> AsyncSandbox:
    if not isinstance(environment, E2BEnvironment):
        raise ValueError("Syndicate requires Harbor's E2B environment")
    # Harbor 0.22.0 exposes no public accessor for its owned sandbox.
    if environment._sandbox is None:
        raise RuntimeError("Harbor E2B environment must be started")
    return environment._sandbox


class SyndicateNexAUAgent(BaseAgent):
    """Harbor extension; Harbor retains sole verifier authority after `run` returns."""

    def __init__(
        self,
        logs_dir: Path,
        request: RuntimeRequest,
        api_key: SecretStr,
        harness_dir: Path = HARNESS_SOURCE,
        framework_lock: Path = FRAMEWORK_LOCK,
        model_name: str | None = None,
        logger: Logger | None = None,
        mcp_servers: list[MCPServerConfig] | None = None,
        skills_dir: str | None = None,
        extra_env: dict[str, str] | None = None,
        load_trajectory: str | Path | None = None,
        environment_logs_dir: PurePosixPath | None = None,
    ) -> None:
        super().__init__(
            logs_dir=logs_dir,
            model_name=model_name,
            logger=logger,
            mcp_servers=mcp_servers,
            skills_dir=skills_dir,
            extra_env=extra_env,
            load_trajectory=load_trajectory,
            environment_logs_dir=environment_logs_dir,
        )
        self.request = request
        self.api_key = api_key
        self.harness_dir = harness_dir
        self.framework_lock = framework_lock
        self.cleanup_receipt: CleanupReceipt | None = None
        self._controller_binding: ControllerTrialBinding | None = None
        self._controller_root: Path | None = None

    @staticmethod
    @override
    def name() -> str:
        return "syndicate-nexau"

    @override
    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        workspace = shlex.quote(
            self.request.baseline.prompt_variables.working_directory
        )
        probe = (
            f"test -d {workspace} && test -r {workspace} && "
            f"test -w {workspace} && test -x {workspace}"
        )
        result = await _sandbox(environment).commands.run(
            'test "$(id -u 10001)" = 10001 && '
            'test "$(id -g 10001)" = 10001 && '
            "command -v bash setpriv setsid timeout pkill pgrep >/dev/null && "
            "setpriv --reuid=10001 --regid=10001 --clear-groups --no-new-privs "
            f"/bin/bash --noprofile --norc -c {shlex.quote(probe)}",
            user="root",
            timeout=5,
        )
        if result.exit_code != 0:
            raise RuntimeError("E2B task identity, tools, or workspace are not ready")

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        self.cleanup_receipt = None
        if instruction != self.request.instruction:
            raise ValueError("Harbor instruction differs from approved runtime request")
        try:
            self.cleanup_receipt = await HarborAgent(
                _sandbox(environment),
                harness_dir=self.harness_dir,
                framework_lock=self.framework_lock,
            ).run(self.request, self.api_key)
        except (Exception, asyncio.CancelledError):
            # Stock Harbor catches timeout/installed-agent exit errors and continues
            # to verification. This adapter requires successful cleanup handoff.
            raise RuntimeError(
                "Controller run failed; verifier handoff blocked"
            ) from None
        if self._controller_binding is not None and self._controller_root is not None:
            emit_cleanup_receipt(
                self._controller_binding,
                self.cleanup_receipt,
                self._controller_root,
                datetime.now(UTC),
            )

    def bind_controller_receipt(
        self, binding: ControllerTrialBinding, controller_root: Path
    ) -> None:
        """Bind controller identity before Harbor starts this agent."""
        self._controller_binding = binding
        self._controller_root = controller_root
