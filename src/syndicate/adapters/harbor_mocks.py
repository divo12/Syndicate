"""Start the seeded ITSM emulator so Harbor can run tests/test.sh."""

import os
import re
import shlex
from pathlib import Path

from e2b import AsyncSandbox
from e2b.sandbox.commands.command_handle import CommandExitException

_TOOLS = re.compile(r'EMULATOR_TOOLS:\s*"([^"]+)"')
_ALIAS = re.compile(r"-\s+([a-z0-9.-]+\.local\.mock)")


def emulator_tools(compose_text: str) -> str:
    match = _TOOLS.search(compose_text)
    if match is None:
        raise ValueError("Task compose does not declare EMULATOR_TOOLS")
    return match.group(1)


def emulator_hosts(compose_text: str) -> tuple[str, ...]:
    hosts = tuple(_ALIAS.findall(compose_text))
    if not hosts:
        raise ValueError("Task compose does not declare mock aliases")
    return hosts


def emulator_start_script(tools: str, hosts: tuple[str, ...]) -> str:
    quoted = " ".join(hosts)
    return (
        "set -e; "
        f"echo 127.0.0.1 {quoted} >> /etc/hosts; "
        "mkdir -p /opt/emulator /task; "
        "tar -C /opt/emulator -xzf /tmp/emulator.tgz; "
        "if ! command -v node >/dev/null; then "
        "curl -fsSL https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-x64.tar.gz "
        "| tar -xz -C /usr/local --strip-components=1; "
        "fi; "
        f"export EMULATOR_TOOLS={shlex.quote(tools)}; "
        "export MOCK_SEED_PATH=/task/seed.json PORT=8080 BIND_HOST=0.0.0.0; "
        "cd /opt/emulator && (nohup node server.js >/tmp/emulator.log 2>&1 &); "
        'set +e; i=0; while [ "$i" -lt 30 ]; do '
        "node -e \"require('http').get('http://127.0.0.1:8080/',r=>process.exit(0))"
        ".on('error',()=>process.exit(1))\" && exit 0; "
        "i=$((i+1)); sleep 1; done; "
        "cat /tmp/emulator.log; exit 1"
    )


def emulator_paths(task_id: str) -> tuple[Path, Path, Path] | None:
    if task_id.strip() == "":
        return None
    root = Path(os.environ.get("BENCHMARK_ROOT", "benchmark/ITSMBench"))
    tarball = Path(os.environ.get("EMULATOR_TARBALL", "/opt/taskgen-emulator.tgz"))
    seed = root / "tasks" / task_id / "environment" / "seed.json"
    compose = root / "tasks" / task_id / "environment" / "docker-compose.yaml"
    if not seed.is_file() or not compose.is_file() or not tarball.is_file():
        return None
    return seed, compose, tarball


async def start_seeded_emulator(sandbox: AsyncSandbox, task_id: str) -> None:
    paths = emulator_paths(task_id)
    if paths is None:
        return
    seed, compose, tarball = paths
    text = compose.read_text(encoding="utf-8")
    await sandbox.files.write("/task/seed.json", seed.read_bytes(), user="root")
    await sandbox.files.write("/tmp/emulator.tgz", tarball.read_bytes(), user="root")
    try:
        await sandbox.commands.run(
            emulator_start_script(emulator_tools(text), emulator_hosts(text)),
            user="root",
            timeout=120,
        )
    except CommandExitException as error:
        raise RuntimeError("Seeded task emulator did not start") from error
