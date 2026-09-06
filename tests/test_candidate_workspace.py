from pathlib import Path

import pytest

from syndicate.candidate_validation import CandidateValidationError, seal_candidate
from syndicate.candidate_workspace import create_candidate_workspace


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


def test_seal_hashes_an_allowlisted_candidate_diff(tmp_path: Path) -> None:
    workspace = create_candidate_workspace(
        _incumbent(tmp_path), ("prompt.md",), tmp_path / "workspaces"
    )
    workspace.candidate_root.joinpath("prompt.md").write_text("candidate")

    first = seal_candidate(workspace)
    second = seal_candidate(workspace)

    assert first == second
    assert first.parent_hash == workspace.incumbent.content_hash
    assert first.changed_paths == ("prompt.md",)
    assert len(first.candidate_hash) == 64
    assert len(first.diff_hash) == 64


@pytest.mark.parametrize("attack", ["protected.py", "nested/link.md"])
def test_seal_rejects_protected_paths_and_symlink_traversal(
    tmp_path: Path, attack: str
) -> None:
    workspace = create_candidate_workspace(
        _incumbent(tmp_path), ("prompt.md",), tmp_path / "workspaces"
    )
    target = workspace.candidate_root / attack
    target.parent.mkdir(parents=True, exist_ok=True)
    if attack == "nested/link.md":
        target.symlink_to(workspace.snapshot_root / "prompt.md")
    else:
        target.write_text("protected edit")

    with pytest.raises(CandidateValidationError):
        seal_candidate(workspace)
