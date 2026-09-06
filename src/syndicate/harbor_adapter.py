"""Native Harbor agent that launches the approved NexAU runtime in a task world."""

import tempfile
from logging import Logger
from pathlib import Path, PurePosixPath
from typing import override
from weakref import WeakKeyDictionary

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.task.config import MCPServerConfig
from harbor.models.verifier.result import VerifierResult
from harbor.verifier.base import BaseVerifier
from pydantic import SecretStr

from syndicate.benchmark import verify_with_harbor
from syndicate.harbor_agent import CleanupReceipt, HarborAgent, runtime_command
from syndicate.runtime_contracts import RuntimeRequest

REQUEST_PATH = "/run/syndicate/request.json"
KEY_PATH = "/run/syndicate/api-key"
HARNESS_PATH = "/run/syndicate/harness"
HARNESS_SOURCE = Path(__file__).parents[2] / "harnesses/seed"


class _CleanupProofs:
    """Transient handoff between Harbor's sequential agent and verifier phases."""

    _receipts: WeakKeyDictionary[BaseEnvironment, CleanupReceipt] = WeakKeyDictionary()

    @classmethod
    def record(cls, environment: BaseEnvironment, receipt: CleanupReceipt) -> None:
        cls._receipts[environment] = receipt

    @classmethod
    def take(cls, environment: BaseEnvironment) -> CleanupReceipt | None:
        return cls._receipts.pop(environment, None)


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
        await environment.upload_dir(HARNESS_SOURCE, HARNESS_PATH)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request_path = root / "request.json"
            key_path = root / "api-key"
            request_path.write_text(self.request.model_dump_json())
            key_path.write_text(self.api_key.get_secret_value())
            await environment.upload_file(request_path, REQUEST_PATH)
            await environment.upload_file(key_path, KEY_PATH)
        await environment.exec(
            command=(f"chown -R 10001:10001 /run/syndicate && chmod 600 {KEY_PATH}"),
            user="root",
        )

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
        _CleanupProofs.record(environment, self.cleanup_receipt)


class SyndicateHarborVerifier(BaseVerifier):
    """Harbor verifier registration that consumes the agent's settled cleanup proof."""

    @classmethod
    def import_path(cls) -> str:
        return f"{cls.__module__}:{cls.__name__}"

    @override
    async def verify(self) -> VerifierResult:
        cleanup = _CleanupProofs.take(self.environment)
        if cleanup is None or self.environment.context_id is None:
            return VerifierResult()
        receipt = await verify_with_harbor(
            self.task,
            self.trial_paths,
            self.environment,
            f"harbor:{self.environment.context_id}",
            cleanup,
        )
        return VerifierResult(
            rewards={"reward": receipt.reward} if receipt.reward is not None else None
        )
