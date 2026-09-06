import subprocess
from collections.abc import Iterator
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from syndicate.benchmark_manifest import (
    ITSMBENCH_REVISION,
    Assignment,
    BenchmarkManifest,
    Split,
)


def test_bundled_benchmark() -> None:
    root = Path(__file__).resolve().parents[1] / "benchmark" / "ITSMBench"
    assert (root / ".git").exists(), (
        "Run git submodule update --init benchmark/ITSMBench"
    )
    manifest = BenchmarkManifest.load(
        root,
        ITSMBENCH_REVISION,
        (Assignment("task-a-1", Split.DEVELOPMENT, "a"),),
    )
    task = manifest.tasks[0]
    assert task.schema_version == "1.3"
    assert task.agent_timeout_sec == 1500
    public = manifest.public_inputs(Split.DEVELOPMENT)
    assert len(public) == 1
    assert public[0].task_id == "task-a-1"
    assert public[0].instruction.strip()
    assert public[0].benchmark_revision == ITSMBENCH_REVISION


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def checkout(tmp_path: Path) -> Iterator[tuple[Path, str]]:
    git(tmp_path, "init", "-q")
    for name in ("task-a-1", "task-b", "task-c"):
        task = tmp_path / "tasks" / name
        task.mkdir(parents=True)
        (task / "instruction.md").write_text("Public goal", encoding="utf-8")
        (task / "task.toml").write_text(
            """schema_version = "1.3"
[task]
name = "owner/example"
[agent]
timeout_sec = 1500.0
[verifier]
timeout_sec = 600.0
[environment]
build_timeout_sec = 600.0
docker_image = "image:declared"
""",
            encoding="utf-8",
        )
        for protected in ("environment", "tests", "solution"):
            (task / protected).mkdir()
            (task / protected / "private").write_text("SECRET", encoding="utf-8")
        (task / "README.md").write_text("SECRET", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(
        tmp_path,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "fixture",
    )
    revision = git(tmp_path, "rev-parse", "HEAD")
    with patch("syndicate.benchmark_manifest.ITSMBENCH_REVISION", revision):
        yield tmp_path, revision


def load(checkout: tuple[Path, str]) -> BenchmarkManifest:
    root, revision = checkout
    with patch("syndicate.benchmark_manifest.ITSMBENCH_REVISION", revision):
        return BenchmarkManifest.load(
            root,
            revision,
            (
                Assignment("task-a-1", Split.DEVELOPMENT, "a"),
                Assignment("task-b", Split.VALIDATION, "b"),
                Assignment("task-c", Split.FINAL_TEST, "c"),
            ),
        )


def test_public_projection_is_immutable_and_split_scoped(
    checkout: tuple[Path, str],
) -> None:
    manifest = load(checkout)
    inputs = manifest.public_inputs(Split.DEVELOPMENT)
    assert len(inputs) == 1
    assert inputs[0].task_id == "task-a-1"
    assert inputs[0].instruction == "Public goal"
    assert "SECRET" not in repr(inputs)
    assert "task-c" not in repr(inputs)
    assert inputs[0].instruction_ref == manifest.tasks[0].instruction_ref
    assert manifest.tasks[0].agent_timeout_sec == 1500
    assert manifest == load(checkout)
    with pytest.raises(FrozenInstanceError):
        inputs[0].instruction = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="final"):
        manifest.public_inputs(Split.FINAL_TEST)
    with pytest.raises(ValueError, match="Split"):
        manifest.public_inputs("development")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "assignments",
    [
        (Assignment("task-a-1", Split.VALIDATION, "a"),),
        (
            Assignment("task-b", Split.DEVELOPMENT, "same"),
            Assignment("task-c", Split.VALIDATION, "same"),
        ),
        (
            Assignment("task-b", Split.DEVELOPMENT, "b"),
            Assignment("task-b", Split.DEVELOPMENT, "b"),
        ),
        (),
    ],
)
def test_invalid_splits(
    checkout: tuple[Path, str], assignments: tuple[Assignment, ...]
) -> None:
    with pytest.raises(ValueError):
        BenchmarkManifest.load(*checkout, assignments)


@pytest.mark.parametrize("task_id", ["../task-a-1", "/tmp/task", "task/a", ""])
def test_task_path_validation(task_id: str) -> None:
    with pytest.raises(ValueError):
        Assignment(task_id, Split.DEVELOPMENT, "family")


def test_revision_and_dirty_checkout_rejected(checkout: tuple[Path, str]) -> None:
    root, _ = checkout
    with pytest.raises(ValueError):
        BenchmarkManifest.load(
            root, "0" * 40, (Assignment("task-b", Split.DEVELOPMENT, "b"),)
        )
    (root / "tasks/task-b/instruction.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="clean"):
        load(checkout)


def test_public_read_rechecks_checkout(checkout: tuple[Path, str]) -> None:
    manifest = load(checkout)
    (checkout[0] / "tasks/task-b/instruction.md").write_text(
        "changed", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="clean"):
        manifest.public_inputs(Split.VALIDATION)


@pytest.mark.parametrize("replacement", ["0", "-1", "nan", "inf", "true", '"600"'])
def test_invalid_declared_timeout(checkout: tuple[Path, str], replacement: str) -> None:
    root, _ = checkout
    path = root / "tasks/task-b/task.toml"
    path.write_text(path.read_text().replace("1500.0", replacement), encoding="utf-8")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "bad",
    )
    with pytest.raises(ValueError):
        load((root, git(root, "rev-parse", "HEAD")))


def test_symlink_instruction_rejected(checkout: tuple[Path, str]) -> None:
    root, _ = checkout
    path = root / "tasks/task-b/instruction.md"
    path.unlink()
    path.symlink_to("README.md")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "link",
    )
    with pytest.raises(ValueError, match="regular"):
        load((root, git(root, "rev-parse", "HEAD")))


def test_forged_instruction_reference_rejected(checkout: tuple[Path, str]) -> None:
    from dataclasses import replace

    manifest = load(checkout)
    task = manifest.tasks[0]
    secret = git(checkout[0], "rev-parse", f"{checkout[1]}:tasks/task-a-1/README.md")
    forged = replace(manifest, tasks=(replace(task, instruction_ref=secret),))
    with pytest.raises(ValueError, match="pinned source"):
        forged.public_inputs(Split.DEVELOPMENT)


def test_original_instruction_whitespace_preserved(checkout: tuple[Path, str]) -> None:
    root, _ = checkout
    (root / "tasks/task-a-1/instruction.md").write_text("  Goal\n\n", encoding="utf-8")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-qm",
        "goal",
    )
    manifest = load((root, git(root, "rev-parse", "HEAD")))
    with patch("syndicate.benchmark_manifest.ITSMBENCH_REVISION", manifest.revision):
        assert manifest.public_inputs(Split.DEVELOPMENT)[0].instruction == "  Goal\n\n"


def test_manifest_hash_pins_assignments(checkout: tuple[Path, str]) -> None:
    from dataclasses import replace

    manifest = load(checkout)
    assert manifest.content_hash == load(checkout).content_hash
    changed = replace(
        manifest,
        tasks=(
            replace(
                manifest.tasks[1],
                assignment=Assignment("task-b", Split.DEVELOPMENT, "b"),
            ),
        ),
    )
    assert changed.content_hash != manifest.content_hash


def test_canonical_pin_rejects_clean_alternate_head(checkout: tuple[Path, str]) -> None:
    from dataclasses import replace

    root, revision = checkout
    manifest = load(checkout)
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "--allow-empty",
        "-qm",
        "alternate",
    )
    alternate = git(root, "rev-parse", "HEAD")
    with patch("syndicate.benchmark_manifest.ITSMBENCH_REVISION", revision):
        with pytest.raises(ValueError, match="canonical"):
            BenchmarkManifest.load(root, alternate, (manifest.tasks[0].assignment,))
        with pytest.raises(ValueError, match="canonical"):
            replace(manifest, revision=alternate).public_inputs(Split.DEVELOPMENT)
