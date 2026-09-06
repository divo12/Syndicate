"""Immutable incumbent snapshot and isolated candidate workspace types."""

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
