"""Harbor-side lifecycle boundary for the unprivileged NexAU process."""

import asyncio

from harbor.environments.base import BaseEnvironment
from pydantic import BaseModel, ConfigDict, Field


class CleanupReceipt(BaseModel):
    """Nonpayload proof that no process remains for the trial user."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    uid: int = Field(gt=0)
    complete: bool


class HarborAgent:
    """Runs Agent A before verifier files exist, then clears its whole UID."""

    def __init__(
        self,
        environment: BaseEnvironment,
        *,
        uid: int = 10001,
        cleanup_timeout_ms: int = 1_000,
    ) -> None:
        if type(cleanup_timeout_ms) is not int or cleanup_timeout_ms <= 0:
            raise ValueError("Positive cleanup timeout required")
        self.environment = environment
        self.uid = uid
        self.cleanup_timeout_ms = cleanup_timeout_ms

    async def assert_hidden_files_absent(self) -> None:
        result = await self.environment.exec(
            command="test ! -e /tests && test ! -e /solution", user=str(self.uid)
        )
        if result.return_code != 0:
            raise PermissionError("Hidden verifier paths are visible to the agent")

    async def run(self, command: str) -> CleanupReceipt:
        await self.assert_hidden_files_absent()
        try:
            await self.environment.exec(command=command, user=str(self.uid))
        finally:
            receipt = await self.cleanup()
        if not receipt.complete:
            raise RuntimeError("Agent UID cleanup is incomplete; verifier is blocked")
        return receipt

    async def cleanup(self) -> CleanupReceipt:
        """Run as Harbor controller authority; setsid descendants share the UID."""
        await self.environment.exec(
            command=f"pkill -KILL -u {self.uid} || true", user="root"
        )
        deadline = asyncio.get_running_loop().time() + self.cleanup_timeout_ms / 1000
        settled = 0
        while asyncio.get_running_loop().time() < deadline:
            result = await self.environment.exec(
                command=f"pgrep -u {self.uid}", user="root"
            )
            settled = settled + 1 if result.return_code == 1 else 0
            if settled == 2:
                return CleanupReceipt(uid=self.uid, complete=True)
            await asyncio.sleep(0.05)
        return CleanupReceipt(uid=self.uid, complete=False)


def runtime_command() -> str:
    """The fixed container entry point; no shell interpolation."""
    return "python -I -m syndicate.nexau_runtime"
