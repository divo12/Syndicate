"""Harbor-side lifecycle boundary for the unprivileged NexAU process."""

from pathlib import Path

from harbor.environments.base import BaseEnvironment
from pydantic import BaseModel, ConfigDict, Field


class CleanupReceipt(BaseModel):
    """Nonpayload proof that no process remains for the trial user."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    uid: int = Field(gt=0)
    complete: bool


class HarborAgent:
    """Runs Agent A before verifier files exist, then clears its whole UID."""

    def __init__(self, environment: BaseEnvironment, *, uid: int = 10001) -> None:
        self.environment = environment
        self.uid = uid

    async def assert_hidden_files_absent(self) -> None:
        await self.environment.exec(
            command="test ! -e /tests && test ! -e /solution", user=str(self.uid)
        )

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
        result = await self.environment.exec(
            command=f"! pgrep -u {self.uid}", user="root"
        )
        return CleanupReceipt(uid=self.uid, complete=result.return_code == 0)


def runtime_command(request_path: Path = Path("/run/syndicate/request.json")) -> str:
    """The fixed container entry point; no host path or shell interpolation."""
    if not request_path.is_absolute() or " " in str(request_path):
        raise ValueError(
            "Runtime request must be an absolute space-free container path"
        )
    return "python -I -m syndicate.nexau_runtime"
