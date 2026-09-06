"""Mine Harbor verifier and agent artifacts into the next-generation prompt."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

_AGENT_MARKERS = (
    "Maximum iteration limit reached",
    "RuntimeStopped",
)


class TaskFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    task_id: str
    source: str
    failed: tuple[str, ...]
    passed: tuple[str, ...]
    agent_note: str = ""


class FailureMine(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    generation: int = Field(ge=0)
    tasks: tuple[TaskFailure, ...]
    lesson: str


def mine_latest(runs_root: Path, generation: int) -> FailureMine:
    latest = _latest_results(runs_root)
    tasks = tuple(
        _parse_trial(task_id, path) for task_id, path in sorted(latest.items())
    )
    return FailureMine(
        generation=generation, tasks=tasks, lesson=_lesson(generation, tasks)
    )


def write_lessons(artifact_root: Path, generation: int, mine: FailureMine) -> Path:
    dest = artifact_root / "harnesses" / f"gen-{generation}.lessons.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(mine.lesson, encoding="utf-8")
    return dest


def lessons_for(artifact_root: Path, generation: int) -> str:
    path = artifact_root / "harnesses" / f"gen-{generation}.lessons.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _latest_results(runs_root: Path) -> dict[str, Path]:
    latest: dict[str, Path] = {}
    if not runs_root.is_dir():
        return latest
    for result in runs_root.glob("**/harbor/trial/result.json"):
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("task_name") or data.get("task_id")
        if not isinstance(name, str) or not name.strip():
            continue
        task_id = name.rstrip("/").rsplit("/", 1)[-1]
        previous = latest.get(task_id)
        if previous is None or result.stat().st_mtime >= previous.stat().st_mtime:
            latest[task_id] = result
    return latest


def _parse_trial(task_id: str, result: Path) -> TaskFailure:
    trial = result.parent
    verifier = trial / "verifier"
    ctrf_failed, ctrf_passed = _ctrf(verifier / "ctrf.json")
    judge_failed, judge_passed = _judge(verifier / "judge_result.json")
    if ctrf_failed or ctrf_passed:
        source = "ctrf"
        failed, passed = ctrf_failed, ctrf_passed
    elif judge_failed or judge_passed:
        source = "judge"
        failed, passed = judge_failed, judge_passed
    else:
        source = "reward"
        failed, passed = ("verifier-reward-zero",), ()
    return TaskFailure(
        task_id=task_id,
        source=source,
        failed=failed,
        passed=passed,
        agent_note=_agent_note(trial),
    )


def _ctrf(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = _json(path)
    if data is None:
        return (), ()
    results = data.get("results")
    tests = results.get("tests") if isinstance(results, dict) else data.get("tests")
    if not isinstance(tests, list):
        return (), ()
    failed: list[str] = []
    passed: list[str] = []
    for item in tests:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name:
            continue
        status = str(item.get("status") or "").lower()
        if status == "passed":
            passed.append(name)
        elif status in {"failed", "error"}:
            failed.append(name)
    return tuple(failed), tuple(passed)


def _judge(path: Path) -> tuple[tuple[str, ...], tuple[str, ...]]:
    data = _json(path)
    if data is None:
        return (), ()
    assertions = data.get("assertions")
    if not isinstance(assertions, list):
        return (), ()
    failed: list[str] = []
    passed: list[str] = []
    for item in assertions:
        if not isinstance(item, dict):
            continue
        params = item.get("params")
        name = params.get("name") if isinstance(params, dict) else None
        if not isinstance(name, str) or not name:
            name = str(item.get("type") or "assertion")
        if item.get("passed") is True:
            passed.append(name)
        else:
            failed.append(name)
    return tuple(failed), tuple(passed)


def _agent_note(trial: Path) -> str:
    log = trial / "trial.log"
    text = log.read_text(encoding="utf-8") if log.is_file() else ""
    for marker in _AGENT_MARKERS:
        if marker in text:
            return marker
    return ""


def _lesson(generation: int, tasks: tuple[TaskFailure, ...]) -> str:
    if not tasks:
        return ""
    lines = [
        f"## Consolidated verifier and agent failures (generation {generation})",
        "",
        "Change the seeded ITSM mocks so these verifier checks pass.",
        "Trust only tests/test.sh (pytest test_outputs.py or grade.js).",
        "Never run solution/solve.sh or any oracle.",
        "Mocks listen on *.local.mock:8080. Inspect with curl, then update records.",
        "",
    ]
    for item in tasks:
        lines.append(f"### {item.task_id}")
        if item.agent_note:
            lines.append(f"Agent: {item.agent_note}")
        if item.failed:
            lines.append("Failed verifier checks:")
            lines.extend(f"- {name}" for name in item.failed)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _json(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
