"""Opt-in full controller/NexAU/E2B cycle with synthetic model responses."""

import asyncio
import os
from unittest.mock import patch

import pytest
from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException
from pydantic import SecretStr
from test_nexau_runtime import ROOT, runtime_fixture

from syndicate.nexau_runtime import run_on_controller


@pytest.mark.skipif(os.environ.get("SYNDICATE_E2B_TEST") != "1", reason="opt-in E2B VM")
def test_live_e2b_tool_cycle_without_paid_model() -> None:
    request, client, calls = runtime_fixture(4)
    request = request.model_copy(update={"shell_timeout_ms": 10000})

    async def run() -> str:
        sandbox = await AsyncSandbox.create(timeout=120)
        try:
            await sandbox.commands.run(
                "useradd -u 10001 -m -s /bin/bash syndicate && "
                "mkdir -p /app && chown 10001:10001 /app",
                user="root",
                timeout=10,
            )
            result = await run_on_controller(
                request,
                SecretStr("fixture"),
                sandbox,
                harness_dir=ROOT / "harnesses/seed",
                framework_lock=ROOT / "requirements.lock",
            )
            with pytest.raises(CommandExitException) as stopped:
                await sandbox.commands.run("pgrep -u 10001", user="root", timeout=5)
            assert stopped.value.exit_code == 1
            return result
        finally:
            await sandbox.kill()

    with (
        patch("syndicate.nexau_runtime.OpenAI", return_value=client),
        asyncio.Runner() as runner,
    ):
        assert runner.run(run()) == "Completed."
    assert len(calls) == 3
    assert b"shell-ok" in calls[2]
