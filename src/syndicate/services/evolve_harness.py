"""Evolve Agent: turn debugger reports into a file-level harness change."""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

EvolveRunner = Callable[[str, Path], str]


class EvolveReceipt(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    dest: Path
    manifest: dict[str, object] = Field(default_factory=dict)


EVOLVE_PROMPT = """You are the NexAU Evolution Engine. Maximize pass@1 of the coding
agent by editing its harness. The base model is fixed.

Only the workspace directory is writable. runs/ and analysis/ are read-only.

Read analysis/overview.md first, then analysis/detail/*.md. Every edit must
record four fields: failure evidence, root cause, targeted fix, predicted impact.

You may add or modify: systemprompt.md, LongTermMEMORY.md, tool_descriptions/,
tools/, skills/, middleware/, sub_agents/, code_agent.yaml.

Choose the component that matches the root cause (prompt, tool, skill, middleware).
Do not hardcode task-specific solutions or run the oracle.

Write change_manifest.json in the workspace with:
{{
  "iteration": {iteration},
  "changes": [
    {{
      "id": "chg-1",
      "type": "new|improvement|rollback",
      "description": "...",
      "files": ["relative/path"],
      "failure_pattern": "...",
      "predicted_fixes": ["task-id"],
      "risk_tasks": ["task-id"],
      "constraint_level": "skill|prompt|tool_impl|tool_desc|middleware",
      "why_this_component": "..."
    }}
  ]
}}

Analysis overview:
{overview}
"""


def evolve_workspace(
    analysis_root: Path,
    seed: Path,
    dest: Path,
    runner: EvolveRunner,
    iteration: int = 1,
) -> EvolveReceipt:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(seed, dest)
    overview = ""
    overview_path = analysis_root / "overview.md"
    if overview_path.is_file():
        overview = overview_path.read_text(encoding="utf-8")
    details = []
    detail = analysis_root / "detail"
    if detail.is_dir():
        for path in sorted(detail.glob("*.md")):
            details.append(f"## {path.stem}\n{path.read_text(encoding='utf-8')}")
    prompt = EVOLVE_PROMPT.format(iteration=iteration, overview=overview)
    if details:
        prompt = prompt + "\n\n" + "\n".join(details)
    runner(prompt, dest)
    manifest: dict[str, object] = {}
    written = dest / "change_manifest.json"
    if written.is_file():
        loaded = json.loads(written.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            manifest = loaded
    _apply_unwritten_changes(seed, dest, manifest)
    return EvolveReceipt(dest=dest, manifest=manifest)


def _apply_unwritten_changes(
    seed: Path, dest: Path, manifest: dict[str, object]
) -> None:
    notes: list[str] = []
    for change in manifest.get("changes") or ():
        if not isinstance(change, dict):
            continue
        files = change.get("files") or ()
        if not isinstance(files, list | tuple):
            continue
        if _already_edited(seed, dest, files):
            continue
        description = change.get("description")
        if isinstance(description, str) and description.strip():
            notes.append(f"### {change.get('id', 'change')}\n{description.strip()}\n")
    if not notes:
        return
    memory = dest / "LongTermMEMORY.md"
    previous = memory.read_text(encoding="utf-8") if memory.is_file() else ""
    memory.write_text(
        previous.rstrip() + "\n\n## Evolved harness notes\n\n" + "\n".join(notes),
        encoding="utf-8",
    )


def _already_edited(
    seed: Path, dest: Path, files: list[object] | tuple[object, ...]
) -> bool:
    for rel in files:
        if not isinstance(rel, str):
            continue
        current = dest / rel
        original = seed / rel
        if current.is_file() and not original.exists():
            return True
        if (
            current.is_file()
            and original.is_file()
            and current.read_bytes() != original.read_bytes()
        ):
            return True
    return False
