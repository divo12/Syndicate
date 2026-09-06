"""Validate a candidate tree and seal its reviewed content."""

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from syndicate.models.candidate import (
    CandidateSeal,
    CandidateValidationError,
    CandidateWorkspace,
)


@dataclass(frozen=True)
class _CandidateFile:
    path: str
    content: bytes


def seal_candidate(workspace: CandidateWorkspace) -> CandidateSeal:
    """Reject unsafe candidate content before producing deterministic hashes."""
    candidate_paths = _candidate_files(workspace.candidate_root)
    allowed_paths = {path.as_posix() for path in workspace.allowed_paths}
    _validate_candidate_paths(candidate_paths, allowed_paths)
    candidate_files = _snapshot_candidate_files(
        workspace.candidate_root, candidate_paths
    )
    confirmed_paths = _candidate_files(workspace.candidate_root)
    _validate_candidate_paths(confirmed_paths, allowed_paths)
    if confirmed_paths != candidate_paths:
        raise CandidateValidationError("candidate changed during sealing")
    if (
        _snapshot_candidate_files(workspace.candidate_root, confirmed_paths)
        != candidate_files
    ):
        raise CandidateValidationError("candidate changed during sealing")
    changed = tuple(
        path.as_posix()
        for path in workspace.allowed_paths
        if _content_for(candidate_files, path.as_posix())
        != (workspace.snapshot_root / path).read_bytes()
    )
    candidate_hash = _hash_files(candidate_files)
    diff_hash = _hash_diff(workspace, candidate_files, changed)
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


def _validate_candidate_paths(paths: tuple[str, ...], allowed_paths: set[str]) -> None:
    if set(paths) - allowed_paths:
        raise CandidateValidationError("candidate changed a protected path")


def _snapshot_candidate_files(
    root: Path, paths: tuple[str, ...]
) -> tuple[_CandidateFile, ...]:
    files: list[_CandidateFile] = []
    for path in paths:
        content = _read_candidate_file(root, path)
        if content is None:
            raise CandidateValidationError("candidate file disappeared during sealing")
        files.append(_CandidateFile(path=path, content=content))
    return tuple(files)


def _content_for(files: tuple[_CandidateFile, ...], path: str) -> bytes | None:
    for file in files:
        if file.path == path:
            return file.content
    return None


def _hash_files(files: tuple[_CandidateFile, ...]) -> str:
    digest = hashlib.sha256()
    for file in files:
        digest.update(file.path.encode())
        digest.update(b"\0")
        digest.update(file.content)
        digest.update(b"\0")
    return digest.hexdigest()


def _hash_diff(
    workspace: CandidateWorkspace,
    files: tuple[_CandidateFile, ...],
    paths: tuple[str, ...],
) -> str:
    digest = hashlib.sha256(workspace.candidate_parent_hash.encode())
    for relative_path in paths:
        digest.update(relative_path.encode())
        digest.update(b"\0")
        content = _content_for(files, relative_path)
        if content is not None:
            digest.update(content)
        else:
            digest.update(b"<deleted>")
        digest.update(b"\0")
    return digest.hexdigest()


def _read_candidate_file(root: Path, relative_path: str) -> bytes | None:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    directory_fd = _open_candidate_root(root, directory_flags)
    try:
        parts = Path(relative_path).parts
        for part in parts[:-1]:
            next_fd = _open_candidate_component(part, directory_flags, directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return _read_regular_file(parts[-1], file_flags, directory_fd)
    finally:
        os.close(directory_fd)


def _open_candidate_root(root: Path, flags: int) -> int:
    try:
        return os.open(root, flags)
    except OSError as error:
        raise CandidateValidationError("candidate must not contain symlinks") from error


def _open_candidate_component(name: str, flags: int, directory_fd: int) -> int:
    try:
        return os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        raise CandidateValidationError("candidate changed during sealing") from None
    except OSError as error:
        raise CandidateValidationError("candidate must not contain symlinks") from error


def _read_regular_file(name: str, flags: int, directory_fd: int) -> bytes | None:
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CandidateValidationError("candidate must not contain symlinks") from error
    try:
        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
            raise CandidateValidationError("candidate must contain regular files")
        with os.fdopen(file_fd, "rb") as file:
            file_fd = -1
            return file.read()
    finally:
        if file_fd >= 0:
            os.close(file_fd)
