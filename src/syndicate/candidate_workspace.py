"""Create an immutable incumbent copy and an isolated editable candidate tree."""

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class IncumbentSnapshot:
    root: Path
    content_hash: str


@dataclass(frozen=True)
class CandidateWorkspace:
    root: Path
    snapshot_root: Path
    candidate_root: Path
    incumbent: IncumbentSnapshot
    candidate_parent_hash: str
    allowed_paths: tuple[PurePosixPath, ...]


def create_candidate_workspace(
    incumbent_root: Path,
    allowed_paths: tuple[str, ...],
    temporary_parent: Path,
) -> CandidateWorkspace:
    """Copy an incumbent once, then expose only declared candidate files."""
    if incumbent_root.is_symlink():
        raise ValueError("incumbent must not be a symlink")
    source = incumbent_root.resolve(strict=True)
    if not source.is_dir():
        raise ValueError("incumbent root must be a directory")
    _reject_symlinks(source)
    allowed = tuple(PurePosixPath(path) for path in allowed_paths)
    _validate_allowed_paths(source, allowed)
    temporary_parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="candidate-", dir=temporary_parent))
    snapshot_root = root / "incumbent"
    candidate_root = root / "candidate"
    shutil.copytree(source, snapshot_root)
    candidate_root.mkdir()
    for relative_path in allowed:
        source_path = snapshot_root / relative_path
        candidate_path = candidate_root / relative_path
        candidate_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, candidate_path)
    content_hash = _hash_tree(snapshot_root)
    _make_read_only(snapshot_root)
    snapshot = IncumbentSnapshot(root=snapshot_root, content_hash=content_hash)
    return CandidateWorkspace(
        root=root,
        snapshot_root=snapshot_root,
        candidate_root=candidate_root,
        incumbent=snapshot,
        candidate_parent_hash=content_hash,
        allowed_paths=allowed,
    )


def _reject_symlinks(root: Path) -> None:
    if root.is_symlink() or any(path.is_symlink() for path in root.rglob("*")):
        raise ValueError("incumbent must not contain symlinks")


def _validate_allowed_paths(root: Path, paths: tuple[PurePosixPath, ...]) -> None:
    if not paths:
        raise ValueError("at least one candidate path is required")
    if len(set(paths)) != len(paths):
        raise ValueError("candidate paths must be unique")
    for path in paths:
        if path.is_absolute() or ".." in path.parts or not (root / path).is_file():
            raise ValueError(f"candidate path must name an incumbent file: {path}")


def _hash_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def _make_read_only(root: Path) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        path.chmod(0o444 if path.is_file() else 0o555)
    root.chmod(0o555)
