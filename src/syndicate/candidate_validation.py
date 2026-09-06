"""Validate a candidate tree and seal its reviewed content."""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from syndicate.candidate_workspace import CandidateWorkspace


class CandidateValidationError(ValueError):
    """Candidate content escaped its declared editable surface."""


@dataclass(frozen=True)
class CandidateSeal:
    parent_hash: str
    candidate_hash: str
    diff_hash: str
    changed_paths: tuple[str, ...]


def seal_candidate(workspace: CandidateWorkspace) -> CandidateSeal:
    """Reject unsafe candidate content before producing deterministic hashes."""
    candidate_paths = _candidate_files(workspace.candidate_root)
    allowed_paths = {path.as_posix() for path in workspace.allowed_paths}
    unexpected = set(candidate_paths) - allowed_paths
    if unexpected:
        raise CandidateValidationError("candidate changed a protected path")
    changed = tuple(
        path.as_posix()
        for path in workspace.allowed_paths
        if _different(workspace, path.as_posix())
    )
    candidate_hash = _hash_files(workspace.candidate_root, candidate_paths)
    diff_hash = _hash_diff(workspace, changed)
    return CandidateSeal(
        parent_hash=workspace.candidate_parent_hash,
        candidate_hash=candidate_hash,
        diff_hash=diff_hash,
        changed_paths=changed,
    )


def _candidate_files(root: Path) -> tuple[str, ...]:
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise CandidateValidationError("candidate must not contain symlinks")
    return tuple(
        path.relative_to(root).as_posix()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


def _different(workspace: CandidateWorkspace, relative_path: str) -> bool:
    candidate_path = workspace.candidate_root / relative_path
    return (
        not candidate_path.is_file()
        or candidate_path.read_bytes()
        != (workspace.snapshot_root / relative_path).read_bytes()
    )


def _hash_files(root: Path, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for relative_path in paths:
        digest.update(relative_path.encode())
        digest.update(b"\0")
        digest.update((root / relative_path).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _hash_diff(workspace: CandidateWorkspace, paths: tuple[str, ...]) -> str:
    digest = hashlib.sha256(workspace.candidate_parent_hash.encode())
    for relative_path in paths:
        digest.update(relative_path.encode())
        digest.update(b"\0")
        candidate_path = workspace.candidate_root / relative_path
        if candidate_path.is_file():
            digest.update(candidate_path.read_bytes())
        else:
            digest.update(b"<deleted>")
        digest.update(b"\0")
    return digest.hexdigest()
