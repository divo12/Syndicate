from pathlib import Path

import pytest

import syndicate.candidate_validation as candidate_validation
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


def test_seal_rechecks_for_symlinks_after_initial_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = create_candidate_workspace(
        _incumbent(tmp_path), ("prompt.md",), tmp_path / "workspaces"
    )
    original_files = candidate_validation._candidate_files
    external_file = tmp_path / "external.txt"
    external_file.write_text("outside", encoding="utf-8")

    def swap_after_scan(root: Path) -> tuple[str, ...]:
        paths = original_files(root)
        candidate = root / "prompt.md"
        candidate.unlink()
        candidate.symlink_to(external_file)
        return paths

    monkeypatch.setattr(candidate_validation, "_candidate_files", swap_after_scan)

    with pytest.raises(CandidateValidationError, match="symlink"):
        seal_candidate(workspace)


def test_seal_rejects_changed_content_during_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = create_candidate_workspace(
        _incumbent(tmp_path), ("prompt.md",), tmp_path / "workspaces"
    )
    reads: list[str] = []
    contents = (b"candidate", b"other", b"third")

    def changing_read(_root: Path, path: str) -> bytes:
        reads.append(path)
        return contents[len(reads) - 1]

    monkeypatch.setattr(candidate_validation, "_read_candidate_file", changing_read)

    with pytest.raises(CandidateValidationError, match="changed"):
        seal_candidate(workspace)

    assert reads == ["prompt.md", "prompt.md"]


def test_seal_rechecks_the_tree_after_capturing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = create_candidate_workspace(
        _incumbent(tmp_path), ("prompt.md",), tmp_path / "workspaces"
    )
    original_files = candidate_validation._candidate_files

    def add_protected_file_after_scan(root: Path) -> tuple[str, ...]:
        paths = original_files(root)
        root.joinpath("protected.py").write_text("escape", encoding="utf-8")
        return paths

    monkeypatch.setattr(
        candidate_validation, "_candidate_files", add_protected_file_after_scan
    )

    with pytest.raises(CandidateValidationError, match="protected"):
        seal_candidate(workspace)


def test_seal_rechecks_content_after_capturing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = create_candidate_workspace(
        _incumbent(tmp_path), ("prompt.md",), tmp_path / "workspaces"
    )
    original_files = candidate_validation._candidate_files
    scans = 0

    def change_file_on_second_scan(root: Path) -> tuple[str, ...]:
        nonlocal scans
        scans += 1
        if scans == 2:
            root.joinpath("prompt.md").write_text("changed", encoding="utf-8")
        return original_files(root)

    monkeypatch.setattr(
        candidate_validation, "_candidate_files", change_file_on_second_scan
    )

    with pytest.raises(CandidateValidationError, match="changed"):
        seal_candidate(workspace)
