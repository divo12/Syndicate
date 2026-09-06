from pathlib import Path

import pytest

from syndicate.services.candidate import create_candidate_workspace


def _incumbent(root: Path) -> Path:
    harness = root / "harness"
    (harness / "skills").mkdir(parents=True)
    (harness / "prompt.md").write_text("incumbent prompt", encoding="utf-8")
    (harness / "skills" / "search.md").write_text("search", encoding="utf-8")
    (harness / "protected.py").write_text("controller", encoding="utf-8")
    return harness


def test_snapshot_is_immutable_and_candidate_contains_only_allowlisted_files(
    tmp_path: Path,
) -> None:
    incumbent = _incumbent(tmp_path)
    workspace = create_candidate_workspace(
        incumbent, ("prompt.md", "skills/search.md"), tmp_path / "workspaces"
    )

    assert workspace.incumbent.content_hash == workspace.candidate_parent_hash
    assert (workspace.snapshot_root / "protected.py").read_text() == "controller"
    assert not (workspace.candidate_root / "protected.py").exists()
    assert (workspace.candidate_root / "prompt.md").read_text() == "incumbent prompt"
    (workspace.candidate_root / "prompt.md").write_text("candidate", encoding="utf-8")
    incumbent.joinpath("prompt.md").write_text("mutated source", encoding="utf-8")

    assert (workspace.snapshot_root / "prompt.md").read_text() == "incumbent prompt"
    assert (workspace.candidate_root / "prompt.md").read_text() == "candidate"


def test_snapshot_rejects_symlinked_incumbent_content(tmp_path: Path) -> None:
    incumbent = _incumbent(tmp_path)
    (incumbent / "link.md").symlink_to(incumbent / "prompt.md")

    with pytest.raises(ValueError, match="symlink"):
        create_candidate_workspace(incumbent, ("prompt.md",), tmp_path / "workspaces")


def test_snapshot_rejects_a_symlinked_incumbent_root(tmp_path: Path) -> None:
    incumbent = _incumbent(tmp_path)
    linked_root = tmp_path / "linked-harness"
    linked_root.symlink_to(incumbent, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        create_candidate_workspace(linked_root, ("prompt.md",), tmp_path / "workspaces")
