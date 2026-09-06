import json
from pathlib import Path

from syndicate.services.debug_corpus import materialize_analysis


def _trial(
    runs: Path,
    task_name: str,
    *,
    stamp: int,
    stdout: str,
    events: list[dict[str, str]] | None = None,
) -> Path:
    trial = runs / f"op-{stamp}" / f"att-{stamp}" / "harbor" / "trial"
    verifier = trial / "verifier"
    agent = trial / "agent"
    verifier.mkdir(parents=True)
    agent.mkdir(parents=True)
    (trial / "result.json").write_text(json.dumps({"task_name": task_name}))
    (verifier / "test-stdout.txt").write_text(stdout)
    (verifier / "reward.txt").write_text("0\n")
    if events is not None:
        (agent / "nexau_in_memory_tracer.json").write_text(
            json.dumps({"events": events})
        )
    (trial / "trial.log").write_text("Maximum iteration limit reached\n")
    return trial


def test_materialize_analysis_lays_out_traces_env_and_verifier(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    _trial(
        runs,
        "abhishek203/task-a-1",
        stamp=1,
        stdout="FAILED test_gw_account_suspended\n",
        events=[{"kind": "shell", "command": "curl http://gw.local.mock:8080/users"}],
    )
    root = materialize_analysis(runs, tmp_path / "analysis")
    task = root / "detail" / "task-a-1"
    assert (task / "verifier" / "test-stdout.txt").read_text().startswith("FAILED")
    assert (task / "environment.md").is_file()
    assert "gw.local.mock" in (task / "agent" / "trace.json").read_text()
    assert (task / "agent" / "messages" / "000.md").is_file()
    assert (root / "overview.md").is_file() is False
