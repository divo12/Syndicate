from pathlib import Path

from syndicate.services.agent_debugger import debug_generation, generate_debug_query
from syndicate.services.debug_corpus import materialize_analysis


def test_generate_debug_query_is_task_specific() -> None:
    query = generate_debug_query(
        "task-b1",
        "FAILED rogue-access-sync-token-revoked\n",
        passed=False,
    )
    assert "task-b1" in query
    assert "rogue-access-sync-token-revoked" in query
    assert "ROOT CAUSE" in query
    assert "verifier" in query.lower()


def test_debug_generation_writes_per_task_reports_from_tool_runner(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    trial = runs / "op" / "att" / "harbor" / "trial"
    (trial / "verifier").mkdir(parents=True)
    (trial / "agent").mkdir()
    (trial / "result.json").write_text('{"task_name": "taskgen/task-c1"}')
    (trial / "verifier" / "test-stdout.txt").write_text(
        "FAILED lockdown-tendai-suspended\n"
    )
    (trial / "verifier" / "reward.txt").write_text("0\n")
    analysis = materialize_analysis(runs, tmp_path / "analysis")

    def runner(task_id: str, query: str, corpus: Path) -> str:
        assert task_id == "task-c1"
        assert "lockdown-tendai-suspended" in query
        assert (corpus / "verifier" / "test-stdout.txt").is_file()
        return (
            "## ROOT CAUSE\nAgent never PATCHed Okta user Tendai to SUSPENDED.\n"
            "## FAILURE POINT\nStopped after listing users.\n"
        )

    reports = debug_generation(analysis, runner)
    assert [path.name for path in reports] == ["task-c1.md"]
    text = (analysis / "detail" / "task-c1.md").read_text(encoding="utf-8")
    assert "Tendai" in text
    assert "ROOT CAUSE" in (analysis / "overview.md").read_text(encoding="utf-8")
