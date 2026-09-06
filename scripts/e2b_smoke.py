"""Opt-in no-model E2B smoke: create a short-lived VM, verify shell, delete VM."""

import asyncio

from e2b import AsyncSandbox

from syndicate.shell import ShellRequest, ShellStatus
from syndicate.shell_backend import E2BShell


async def smoke() -> None:
    sandbox = await AsyncSandbox.create(timeout=120)
    try:
        await sandbox.commands.run(
            "useradd -u 10001 -m -s /bin/bash syndicate && "
            "mkdir -p /work/sub && chown -R 10001:10001 /work",
            user="root",
            timeout=10,
        )
        shell = E2BShell(sandbox, "/work")
        result = await shell.execute(
            ShellRequest(command="pwd; printf remote-ok", dir_path="sub"), 10000
        )
        assert result.stdout == "/work/sub\nremote-ok", result
        assert result.exit_code == 0
        print("remote output and working directory: passed")
        root_shell = E2BShell(sandbox, "/")
        root_result = await root_shell.execute(
            ShellRequest(command="pwd", dir_path="/work"), 10000
        )
        assert root_result.stdout == "/work\n"
        rejected = await shell.execute(
            ShellRequest(command="echo must-not-run", dir_path="/"), 10000
        )
        assert rejected.exit_code == 125
        await shell.execute(ShellRequest(command="sleep 60", is_background=True), 10000)
        await shell.close()
        assert shell.cleanup_complete
        print("background UID cleanup and cwd confinement: passed")
        limited = E2BShell(sandbox, "/work", capture_limit_bytes=16)
        result = await limited.execute(ShellRequest(command="yes output"), 10000)
        assert result.status == ShellStatus.ERROR
        assert len(result.stdout.encode()) == 16
        await limited.close()
        print("bounded streaming output: passed")
    finally:
        await sandbox.kill()
        print("smoke sandbox deleted")


if __name__ == "__main__":
    asyncio.run(smoke())
