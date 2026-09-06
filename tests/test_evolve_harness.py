import json
from pathlib import Path
from shutil import copytree

from syndicate.services.evolve_harness import evolve_workspace

ROOT = Path(__file__).resolve().parents[1]


def test_evolve_workspace_applies_harness_file_edits(tmp_path: Path) -> None:
    analysis = tmp_path / "analysis"
    (analysis / "detail").mkdir(parents=True)
    (analysis / "overview.md").write_text(
        "Pattern: agents inspect mocks then stop without PATCH.\n"
    )
    (analysis / "detail" / "task-a-1.md").write_text(
        "ROOT CAUSE: no workflow for mutating seeded ITSM records.\n"
    )
    dest = tmp_path / "harnesses" / "gen-1"

    def runner(prompt: str, workspace: Path) -> str:
        assert "task-a-1" in prompt
        skill = workspace / "skills" / "itsm-mocks" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            "Inspect *.local.mock:8080 then PATCH the records verifier names.\n"
        )
        memory = workspace / "LongTermMEMORY.md"
        memory.write_text(memory.read_text() + "\nUse the itsm-mocks skill.\n")
        manifest = {
            "iteration": 1,
            "changes": [
                {
                    "id": "chg-1",
                    "type": "new",
                    "description": "Add ITSM mock workflow skill",
                    "files": ["skills/itsm-mocks/SKILL.md", "LongTermMEMORY.md"],
                    "failure_pattern": "inspect-then-stop",
                    "predicted_fixes": ["task-a-1"],
                    "risk_tasks": ["task-a-2"],
                    "constraint_level": "skill",
                    "why_this_component": "Reusable workflow, not a prompt hack",
                }
            ],
        }
        (workspace / "change_manifest.json").write_text(json.dumps(manifest))
        return "added skill"

    receipt = evolve_workspace(
        analysis, copytree(ROOT / "harnesses" / "seed", tmp_path / "seed"), dest, runner
    )
    assert receipt.dest == dest
    assert (dest / "skills" / "itsm-mocks" / "SKILL.md").is_file()
    assert "itsm-mocks" in (dest / "LongTermMEMORY.md").read_text()
    assert json.loads((dest / "change_manifest.json").read_text())["changes"][0][
        "constraint_level"
    ] == "skill"


def test_evolve_applies_manifest_notes_when_files_were_not_edited(
    tmp_path: Path,
) -> None:
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "overview.md").write_text("inspect-then-stop\n")
    dest = tmp_path / "gen-1"
    seed = copytree(ROOT / "harnesses" / "seed", tmp_path / "seed")

    def runner(prompt: str, workspace: Path) -> str:
        del prompt
        (workspace / "change_manifest.json").write_text(
            json.dumps(
                {
                    "iteration": 1,
                    "changes": [
                        {
                            "id": "chg-1",
                            "description": "Force PATCH after discovery.",
                            "files": ["systemprompt.md"],
                        }
                    ],
                }
            )
        )
        return "manifest only"

    evolve_workspace(analysis, seed, dest, runner)
    assert "Force PATCH after discovery." in (dest / "LongTermMEMORY.md").read_text()
