"""P06 contracts: exact upstream assets, reproducible identity, fail-closed inputs."""

from pathlib import Path
from shutil import copytree

import pytest

from syndicate.baseline import BaselineStage, prepare_baseline
from syndicate.model_config import ModelSettings

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ModelSettings(endpoint="https://example.com/", deployment="gpt-5.4-mini")


def test_pinned_seed_and_manifest_are_reproducible(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    copytree(ROOT / "harnesses/seed", seed)
    first = prepare_baseline(seed, ROOT / "requirements.lock", SETTINGS)
    second = prepare_baseline(seed, ROOT / "requirements.lock", SETTINGS)
    assert first == second
    assert first.stage is BaselineStage.PREPARED
    assert first.ahe_revision == "8b2a55d97590363fe50c3cc6b5e833b020a4bb4c"
    assert first.nexau_revision == "35ee1861546db3cb280a6e17e38a74060d7c96c3"
    assert len(first.identity_hash) == 64
    assert first.compatibility_diff == ""
    assert first.model.requested_model == "gpt-5.4-mini"
    with pytest.raises(ValueError):
        first.stage = BaselineStage.PREPARED  # type: ignore[misc]
    restored = type(first).model_validate_json(first.model_dump_json())
    assert restored.identity_hash == first.identity_hash


@pytest.mark.parametrize("mutation", ["changed", "missing", "extra", "symlink"])
def test_reject_modified_seed(tmp_path: Path, mutation: str) -> None:
    seed = tmp_path / "seed"
    copytree(ROOT / "harnesses/seed", seed)
    prompt = seed / "systemprompt.md"
    if mutation == "changed":
        prompt.write_text("unapproved prompt adaptation")
    elif mutation == "missing":
        prompt.unlink()
    elif mutation == "extra":
        (seed / "unrecorded.txt").write_text("extra")
    else:
        prompt.unlink()
        prompt.symlink_to(ROOT / "harnesses/seed/systemprompt.md")
    with pytest.raises(ValueError, match="seed"):
        prepare_baseline(seed, ROOT / "requirements.lock", SETTINGS)


def test_settings_and_lock_change_identity(tmp_path: Path) -> None:
    seed = ROOT / "harnesses/seed"
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==2.11.7\n")
    first = prepare_baseline(seed, lock, SETTINGS)
    lock.write_text("pydantic==2.11.8\n")
    assert first.identity_hash != prepare_baseline(seed, lock, SETTINGS).identity_hash
    other = ModelSettings(
        endpoint="https://other.example.com/", deployment="gpt-5.4-mini"
    )
    assert first.identity_hash != prepare_baseline(seed, lock, other).identity_hash


def test_missing_lock_is_not_a_valid_baseline(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        prepare_baseline(ROOT / "harnesses/seed", tmp_path / "missing", SETTINGS)
