from pathlib import Path

import pytest

from syndicate.harbor_agent import runtime_command


def test_runtime_command_is_fixed_container_module() -> None:
    assert runtime_command() == "python -I -m syndicate.nexau_runtime"


def test_runtime_command_rejects_host_like_request_path() -> None:
    with pytest.raises(ValueError, match="absolute"):
        runtime_command(Path("request.json"))
