import importlib.util
from pathlib import Path
from typing import Protocol, cast


class _Client(Protocol):
    def terminal_exit(self, status: str) -> int: ...


def test_job_client_exits_nonzero_when_the_job_did_not_complete() -> None:
    path = Path(__file__).resolve().parents[1] / "scripts" / "job_client.py"
    spec = importlib.util.spec_from_file_location("job_client", path)
    if spec is None or spec.loader is None:
        raise AssertionError("job_client module is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    client = cast(_Client, module)
    assert client.terminal_exit("completed") == 0
    assert client.terminal_exit("failed") == 1
    assert client.terminal_exit("cancelled") == 1
