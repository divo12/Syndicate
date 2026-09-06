"""Private benchmark registry with an instruction-only public projection."""

import hashlib
import json
import math
import re
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import cast

ITSMBENCH_REVISION = "30da7457d5479d0bcfae40dece7bd85d66df4401"


class Split(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    FINAL_TEST = "final_test"


@dataclass(frozen=True, slots=True)
class Assignment:
    task_id: str
    split: Split
    family: str

    def __post_init__(self) -> None:
        if not re.fullmatch(r"task-[a-zA-Z0-9-]+", self.task_id):
            raise ValueError("Invalid task ID")
        if not isinstance(self.split, Split) or not self.family.strip():
            raise ValueError("Explicit Split and family required")


@dataclass(frozen=True, slots=True)
class PublicTaskInput:
    task_id: str
    split: Split
    benchmark_revision: str
    instruction_ref: str
    instruction: str


@dataclass(frozen=True, slots=True)
class TaskManifest:
    assignment: Assignment
    task_ref: str
    instruction_ref: str
    environment_ref: str
    verifier_ref: str
    solution_ref: str
    schema_version: str
    name: str
    docker_image: str
    agent_timeout_sec: float
    verifier_timeout_sec: float
    build_timeout_sec: float


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), *args],
            text=True,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        raise ValueError("Benchmark Git lookup failed") from exc


def _check_checkout(root: Path, revision: str) -> None:
    if revision != ITSMBENCH_REVISION:
        raise ValueError("Benchmark revision must match canonical ITSMBench pin")
    if _git(root, "rev-parse", "HEAD").strip() != revision:
        raise ValueError("Benchmark revision mismatch")
    if _git(root, "status", "--porcelain", "--untracked-files=all", "--ignored"):
        raise ValueError("Benchmark checkout must be clean")


def _ref(root: Path, revision: str, relative: str, mode: str) -> str:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Benchmark path must be contained and regular")
    entry = _git(root, "ls-tree", revision, "--", relative).split()
    if len(entry) != 4 or entry[0] != mode:
        raise ValueError("Missing or non-regular benchmark path")
    return entry[2]


def _table(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Metadata table required")
    return cast(Mapping[str, object], value)


def _text(table: Mapping[str, object], key: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing metadata string: {key}")
    return value


def _timeout(table: Mapping[str, object], section: str, key: str) -> float:
    value = _table(table.get(section)).get(key)
    if type(value) not in (int, float):
        raise ValueError(f"Numeric {section}.{key} required")
    number = float(cast(int | float, value))
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"Positive finite {section}.{key} required")
    return number


def _task(root: Path, revision: str, assignment: Assignment) -> TaskManifest:
    relative = f"tasks/{assignment.task_id}"
    task_ref = _ref(root, revision, relative, "040000")
    metadata_ref = _ref(root, revision, f"{relative}/task.toml", "100644")
    try:
        metadata = _table(tomllib.loads(_git(root, "show", metadata_ref)))
    except tomllib.TOMLDecodeError as exc:
        raise ValueError("Invalid task metadata") from exc
    version = _text(metadata, "schema_version")
    if version != "1.3":
        raise ValueError("Unsupported task schema version")
    return TaskManifest(
        assignment=assignment,
        task_ref=task_ref,
        instruction_ref=_ref(root, revision, f"{relative}/instruction.md", "100644"),
        environment_ref=_ref(root, revision, f"{relative}/environment", "040000"),
        verifier_ref=_ref(root, revision, f"{relative}/tests", "040000"),
        solution_ref=_ref(root, revision, f"{relative}/solution", "040000"),
        schema_version=version,
        name=_text(_table(metadata.get("task")), "name"),
        docker_image=_text(_table(metadata.get("environment")), "docker_image"),
        agent_timeout_sec=_timeout(metadata, "agent", "timeout_sec"),
        verifier_timeout_sec=_timeout(metadata, "verifier", "timeout_sec"),
        build_timeout_sec=_timeout(metadata, "environment", "build_timeout_sec"),
    )


def _validate_assignments(assignments: tuple[Assignment, ...]) -> None:
    if not isinstance(assignments, tuple) or not assignments:
        raise ValueError("Nonempty immutable assignments required")
    if len({item.task_id for item in assignments}) != len(assignments):
        raise ValueError("Duplicate task assignment")
    _validate_families(assignments)
    if any(
        item.task_id == "task-a-1" and item.split != Split.DEVELOPMENT
        for item in assignments
    ):
        raise ValueError("task-a-1 is development-only")


def _validate_families(assignments: tuple[Assignment, ...]) -> None:
    families = {item.family for item in assignments}
    groups = {(item.family, item.split) for item in assignments}
    if len(families) != len(groups):
        raise ValueError("Task family crosses splits")


@dataclass(frozen=True, slots=True)
class BenchmarkManifest:
    """Private controller record, never serialize this registry into role requests.

    Git tree IDs pin protected content without reading answers. Declared image
    tags are metadata, not resolved runtime digests. Campaign budgets are separate.
    load() is trusted controller construction from operator-declared assignments.
    The frozen tuple and content_hash seal that declaration; later campaign
    admission must compare against its retained approved hash, not redeclare splits.
    """

    root: Path
    revision: str
    tasks: tuple[TaskManifest, ...]

    @property
    def content_hash(self) -> str:
        assignments = tuple(
            (
                task.assignment.task_id,
                task.assignment.split,
                task.assignment.family,
                task.task_ref,
            )
            for task in self.tasks
        )
        payload = json.dumps((1, self.revision, assignments), separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    @classmethod
    def load(
        cls,
        root: Path,
        revision: str,
        assignments: tuple[Assignment, ...],
    ) -> "BenchmarkManifest":
        _validate_assignments(assignments)
        root = root.resolve(strict=True)
        _check_checkout(root, revision)
        tasks = tuple(_task(root, revision, item) for item in assignments)
        return cls(root, revision, tasks)

    def _validate_source(self) -> None:
        """Recheck the checkout and records before exposing any instructions."""
        _validate_assignments(tuple(task.assignment for task in self.tasks))
        _check_checkout(self.root, self.revision)
        for task in self.tasks:
            if task != _task(self.root, self.revision, task.assignment):
                raise ValueError("Task manifest does not match pinned source")

    def public_inputs(self, split: Split) -> tuple[PublicTaskInput, ...]:
        """Pre-selection projection; final-test execution needs a controller gate."""
        if not isinstance(split, Split):
            raise ValueError("Explicit Split required")
        if split == Split.FINAL_TEST:
            raise ValueError("Locked final-test tasks unavailable before selection")
        self._validate_source()
        return tuple(
            PublicTaskInput(
                task_id=task.assignment.task_id,
                split=split,
                benchmark_revision=self.revision,
                instruction_ref=task.instruction_ref,
                instruction=_git(self.root, "show", task.instruction_ref),
            )
            for task in self.tasks
            if task.assignment.split == split
        )
