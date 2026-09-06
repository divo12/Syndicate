"""Native Harbor agent that launches the approved NexAU runtime in a task world."""

import tempfile
from logging import Logger
from pathlib import Path, PurePosixPath
from typing import override

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import MCPServerConfig
from harbor.models.task.task import Task
from harbor.models.trial.paths import TrialPaths
from pydantic import SecretStr

from syndicate.benchmark import VerifierReceipt, verify_with_harbor
from syndicate.harbor_agent import CleanupReceipt, HarborAgent, runtime_command
from syndicate.runtime_contracts import RuntimeRequest

REQUEST_PATH = "/run/syndicate/request.json"
KEY_PATH = "/run/syndicate/api-key"


class SyndicateNexAUAgent(BaseAgent):
    """Harbor extension; Harbor retains sole verifier authority after `run` returns."""

    def __init__(
        self,
        logs_dir: Path,
        request: RuntimeRequest,
        api_key: SecretStr,
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
        self.cleanup_receipt: CleanupReceipt | None = None

    @staticmethod
    @override
    def name() -> str:
        return "syndicate-nexau"

    @override
    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        await environment.exec(command="mkdir -p /run/syndicate", user="root")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            key_path = root / "api-key"
            request_path.write_text(self.request.model_dump_json())
            key_path.write_text(self.api_key.get_secret_value())
            await environment.upload_file(request_path, REQUEST_PATH)
            await environment.upload_file(key_path, KEY_PATH)
        await environment.exec(command=f"chmod 600 {KEY_PATH}", user="root")

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if instruction != self.request.instruction:
            raise ValueError("Harbor instruction differs from approved runtime request")
        self.cleanup_receipt = await HarborAgent(environment).run(runtime_command())

    async def verify(
        self,
        task: Task,
        paths: TrialPaths,
        environment: BaseEnvironment,
        raw_result_ref: str,
    ) -> VerifierReceipt:
        """Preserve Harbor's verifier and require this run's cleanup proof."""
        if self.cleanup_receipt is None:
            raise RuntimeError("Agent cleanup proof is required before verification")
        return await verify_with_harbor(
            task, paths, environment, raw_result_ref, self.cleanup_receipt
        )
