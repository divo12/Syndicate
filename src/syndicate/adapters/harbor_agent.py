"""Controller-side task execution and cleanup proof for Harbor handoff."""

from pathlib import Path
from typing import Literal

from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException
from pydantic import BaseModel, ConfigDict, SecretStr

from syndicate.adapters.harbor_mocks import start_seeded_emulator
from syndicate.models.runtime import RuntimeRequest
from syndicate.services.runtime import RuntimeStopped, run_on_controller


class CleanupReceipt(BaseModel):
    """Issued only after the controller runner's E2B cleanup completes."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    uid: Literal[10001] = 10001
    complete: bool


class HarborAgent:
    """Reuse controller execution without uploading credentials or agent code."""

    def __init__(
        self,
        sandbox: AsyncSandbox,
        *,
        harness_dir: Path,
        framework_lock: Path,
        task_id: str = "",
    ) -> None:
        self.sandbox = sandbox
        self.harness_dir = harness_dir
        self.framework_lock = framework_lock
        self.task_id = task_id

    async def assert_hidden_files_absent(self) -> None:
        try:
            await self.sandbox.commands.run(
                "test ! -e /tests && test ! -L /tests && "
                "test ! -e /solution && test ! -L /solution",
                user="root",
                timeout=5,
            )
        except CommandExitException:
            raise PermissionError("Hidden verifier paths are present") from None

    async def run(self, request: RuntimeRequest, key: SecretStr) -> CleanupReceipt:
        await self.assert_hidden_files_absent()
        await start_seeded_emulator(self.sandbox, self.task_id)
        try:
            await run_on_controller(
                request,
                key,
                self.sandbox,
                harness_dir=self.harness_dir,
                framework_lock=self.framework_lock,
            )
        except RuntimeStopped:
            # Agent did not solve; ShellBinding already verified UID cleanup.
            pass
        return CleanupReceipt(complete=True)
