from pathlib import Path

import pytest

from syndicate.adapters.harbor_mocks import (
    emulator_hosts,
    emulator_paths,
    emulator_start_script,
    emulator_tools,
)

_COMPOSE = """
services:
  mock-api:
    environment:
      EMULATOR_TOOLS: "servicenow,okta"
    networks:
      default:
        aliases:
          - servicenow.local.mock
          - okta.local.mock
"""


def test_compose_parser_reads_tools_and_hosts() -> None:
    assert emulator_tools(_COMPOSE) == "servicenow,okta"
    assert emulator_hosts(_COMPOSE) == (
        "servicenow.local.mock",
        "okta.local.mock",
    )


def test_start_script_uses_real_emulator_not_empty_stub() -> None:
    script = emulator_start_script("servicenow,okta", ("okta.local.mock",))
    assert "node server.js" in script
    assert "linux-x64.tar.gz" in script
    assert "tar.xz" not in script
    assert "MOCK_SEED_PATH=/task/seed.json" in script
    assert "okta.local.mock" in script
    assert "itsm-mock" not in script
    assert "solve.sh" not in script


def test_emulator_paths_skip_without_task_or_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BENCHMARK_ROOT", str(tmp_path / "missing"))
    monkeypatch.setenv("EMULATOR_TARBALL", str(tmp_path / "no.tgz"))
    assert emulator_paths("") is None
    assert emulator_paths("task-a-1") is None
