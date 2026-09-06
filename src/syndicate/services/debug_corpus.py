"""Lay out Harbor traces, environment notes, and verifier files for the debugger."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from syndicate.services.failure_mine import mine_latest


def materialize_analysis(runs_root: Path, analysis_root: Path) -> Path:
    """Copy the newest trial per task into analysis/detail/{task}/."""
    analysis_root.mkdir(parents=True, exist_ok=True)
    detail = analysis_root / "detail"
    detail.mkdir(parents=True, exist_ok=True)
    mine = mine_latest(runs_root, 0)
    for item in mine.tasks:
        trial = _latest_trial(runs_root, item.task_id)
        if trial is None:
            continue
        dest = detail / item.task_id
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        _copy_verifier(trial / "verifier", dest / "verifier")
        _write_trace(trial / "agent", dest / "agent")
        (dest / "environment.md").write_text(
            _environment_note(item.task_id), encoding="utf-8"
        )
        (dest / "failures.md").write_text(_failures(item), encoding="utf-8")
        instruction = _instruction(item.task_id)
        if instruction:
            (dest / "instruction.md").write_text(instruction, encoding="utf-8")
    return analysis_root


def _latest_trial(runs_root: Path, task_id: str) -> Path | None:
    latest: Path | None = None
    latest_mtime = -1.0
    if not runs_root.is_dir():
        return None
    for result in runs_root.glob("**/harbor/trial/result.json"):
        try:
            data = json.loads(result.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        name = data.get("task_name") or data.get("task_id")
        if not isinstance(name, str):
            continue
        if name.rstrip("/").rsplit("/", 1)[-1] != task_id:
            continue
        mtime = result.stat().st_mtime
        if mtime >= latest_mtime:
            latest = result.parent
            latest_mtime = mtime
    return latest


def _copy_verifier(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True)
    if not source.is_dir():
        return
    for path in source.iterdir():
        if path.is_file():
            shutil.copy2(path, dest / path.name)


def _write_trace(source: Path, dest: Path) -> None:
    dest.mkdir(parents=True)
    raw = source / "nexau_in_memory_tracer.json"
    events: list[object] = []
    if raw.is_file():
        shutil.copy2(raw, dest / "trace.json")
        try:
            payload = json.loads(raw.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            found = payload.get("events") or payload.get("spans") or payload
            if isinstance(found, list):
                events = found
    messages = dest / "messages"
    messages.mkdir()
    if not events:
        (messages / "000.md").write_text(
            "No structured agent trace was persisted for this trial.\n",
            encoding="utf-8",
        )
        return
    for index, event in enumerate(events):
        body = (
            json.dumps(event, indent=2)
            if not isinstance(event, str)
            else event
        )
        (messages / f"{index:03d}.md").write_text(body + "\n", encoding="utf-8")


def _environment_note(task_id: str) -> str:
    return (
        f"# Environment for {task_id}\n\n"
        "Harbor starts the seeded ITSM emulator before the agent.\n"
        "Mocks listen on `*.local.mock:8080` (ServiceNow, Okta, Google Workspace, "
        "CrowdStrike, Defender, Intune, Snipe-IT).\n"
        "The seed is `/task/seed.json` inside the sandbox. The agent must inspect "
        "and mutate those records. Never run `solution/solve.sh` or any oracle.\n"
        "The verifier is `tests/test.sh` (pytest `test_outputs.py` or `grade.js`).\n"
    )


def _failures(item: object) -> str:
    failed = getattr(item, "failed", ())
    note = getattr(item, "agent_note", "")
    lines = ["# Verifier and agent failure pointers\n"]
    if note:
        lines.append(f"Agent: {note}\n")
    for name in failed:
        lines.append(f"- {name}")
    return "\n".join(lines) + "\n"


def _instruction(task_id: str) -> str:
    from os import environ

    root = Path(environ.get("BENCHMARK_ROOT", "benchmark/ITSMBench"))
    path = root / "tasks" / task_id / "instruction.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")
