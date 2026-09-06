"""Per-task Agent Debugger: generate a judge brief, then inspect the corpus."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

DebugRunner = Callable[[str, str, Path], str]

DEBUG_QUERY = """This task ({task_id}) has a single rollout which {status}.

The corpus at this working directory contains:
- instruction.md — the task the coding agent saw
- environment.md — how the seeded ITSM emulator is wired
- verifier/ — external tests/test.sh output the agent never sees
- failures.md — extracted failed check names
- agent/trace.json and agent/messages/ — the coding agent's tool steps

Use list_dir, read_file, and grep_files to inspect those files. Do not guess.
Cross-reference verifier failures with the agent trace.

Identify:
1. FAILURE POINT: At which exact step did things start going wrong?
2. ROOT CAUSE: Why it failed. Distinguish 'agent thought it succeeded' vs errors.
3. WHAT SHOULD HAVE BEEN DONE: The correct approach at the failure point.
4. GENERAL LESSON: A harness-level mechanism (prompt, tool, skill, middleware)
   that would prevent this class of failure — not a task-specific cheat.

Verifier output:
{verifier}

Write a concise sourced report (under 400 words).
"""


def generate_debug_query(task_id: str, verifier_text: str, *, passed: bool) -> str:
    status = "PASSED" if passed else "FAILED"
    return DEBUG_QUERY.format(
        task_id=task_id, status=status, verifier=_clip(verifier_text)
    )


def _clip(text: str, limit: int = 4000) -> str:
    body = text.strip() or "(missing)"
    if len(body) <= limit:
        return body
    return "... (truncated) ...\n" + body[-limit:]


def debug_generation(analysis_root: Path, runner: DebugRunner) -> tuple[Path, ...]:
    detail = analysis_root / "detail"
    reports: list[Path] = []
    overviews: list[str] = []
    if not detail.is_dir():
        return ()
    for corpus in sorted(path for path in detail.iterdir() if path.is_dir()):
        verifier = corpus / "verifier" / "test-stdout.txt"
        text = verifier.read_text(encoding="utf-8") if verifier.is_file() else ""
        reward = corpus / "verifier" / "reward.txt"
        passed = reward.is_file() and reward.read_text(encoding="utf-8").strip() == "1"
        query = generate_debug_query(corpus.name, text, passed=passed)
        report = runner(corpus.name, query, corpus)
        dest = detail / f"{corpus.name}.md"
        dest.write_text(report.strip() + "\n", encoding="utf-8")
        reports.append(dest)
        overviews.append(f"### {corpus.name}\n{report.strip()}\n")
    if overviews:
        (analysis_root / "overview.md").write_text(
            "# Agent Debugger overview\n\n" + "\n".join(overviews),
            encoding="utf-8",
        )
    return tuple(reports)
