import json
from pathlib import Path

from syndicate.services.failure_mine import (
    lessons_for,
    mine_latest,
    write_lessons,
)


def _trial(
    root: Path,
    task_name: str,
    *,
    ctrf_failed: list[str] | None = None,
    ctrf_passed: list[str] | None = None,
    judge_failed: list[str] | None = None,
    judge_passed: list[str] | None = None,
    agent_log: str = "",
    stamp: int = 1,
) -> Path:
    trial = root / f"op-{stamp}" / f"att-{stamp}" / "harbor" / "trial"
    verifier = trial / "verifier"
    verifier.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": task_name,
                "verifier_result": {"rewards": {"reward": 0.0}},
            }
        )
    )
    if ctrf_failed is not None or ctrf_passed is not None:
        tests = [
            f'{{"name": "{name}", "status": "failed"}}' for name in ctrf_failed or []
        ] + [f'{{"name": "{name}", "status": "passed"}}' for name in ctrf_passed or []]
        (verifier / "ctrf.json").write_text(
            '{"results": {"tests": [' + ", ".join(tests) + "]}}"
        )
    if judge_failed is not None or judge_passed is not None:
        assertions = []
        for name in judge_failed or []:
            assertions.append(
                '{"passed": false, "params": {"name": "'
                + name
                + '"}, "explanation": "missing"}'
            )
        for name in judge_passed or []:
            assertions.append('{"passed": true, "params": {"name": "' + name + '"}}')
        (verifier / "judge_result.json").write_text(
            '{"score": 0, "task_completed_correctly": 0, "assertions": ['
            + ", ".join(assertions)
            + "]}"
        )
    (trial / "trial.log").write_text(agent_log)
    (verifier / "reward.txt").write_text("0\n")
    return trial


def test_mine_latest_consolidates_pytest_and_grade_failures(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _trial(
        runs,
        "abhishek203/task-a-1",
        ctrf_failed=["test_outputs.py::test_gw_account_suspended"],
        ctrf_passed=["test_outputs.py::test_seed_present"],
        agent_log="Maximum iteration limit reached",
        stamp=1,
    )
    _trial(
        runs,
        "taskgen/task-b1",
        judge_failed=["rogue-access-sync-token-revoked"],
        judge_passed=["seed-ok"],
        stamp=2,
    )
    mine = mine_latest(runs, 0)
    assert [item.task_id for item in mine.tasks] == ["task-a-1", "task-b1"]
    assert mine.tasks[0].failed == ("test_outputs.py::test_gw_account_suspended",)
    assert mine.tasks[0].agent_note == "Maximum iteration limit reached"
    assert mine.tasks[1].failed == ("rogue-access-sync-token-revoked",)
    assert "task-a-1" in mine.lesson
    assert "test_gw_account_suspended" in mine.lesson
    assert "rogue-access-sync-token-revoked" in mine.lesson
    assert "tests/test.sh" in mine.lesson


def test_mine_latest_keeps_newest_trial_per_task(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _trial(
        runs,
        "abhishek203/task-a-1",
        ctrf_failed=["old-failure"],
        stamp=1,
    )
    newer = _trial(
        runs,
        "abhishek203/task-a-1",
        ctrf_failed=["new-failure"],
        stamp=2,
    )
    newer_result = newer / "result.json"
    newer_result.touch()
    mine = mine_latest(runs, 0)
    assert len(mine.tasks) == 1
    assert mine.tasks[0].failed == ("new-failure",)


def test_write_lessons_are_read_back_for_the_next_generation(
    tmp_path: Path,
) -> None:
    runs = tmp_path / "runs"
    _trial(
        runs,
        "abhishek203/task-a-5",
        ctrf_failed=["test_outputs.py::test_servicenow_incident_state_resolved"],
        stamp=1,
    )
    mine = mine_latest(runs, 0)
    path = write_lessons(tmp_path, 1, mine)
    assert path.name == "gen-1.lessons.md"
    assert lessons_for(tmp_path, 0) == ""
    assert "test_servicenow_incident_state_resolved" in lessons_for(tmp_path, 1)


def test_mine_latest_is_empty_without_harbor_trials(tmp_path: Path) -> None:
    mine = mine_latest(tmp_path / "missing", 0)
    assert mine.tasks == ()
    assert mine.lesson == ""
