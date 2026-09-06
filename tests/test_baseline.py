"""P06 contracts: exact upstream assets, reproducible identity, fail-closed inputs."""

from datetime import date
from pathlib import Path
from shutil import copytree

import pytest

from syndicate.models.baseline import BaselineStage, PromptVariables, prepare_baseline
from syndicate.models.model_config import ModelSettings

ROOT = Path(__file__).resolve().parents[1]
VARIABLES = PromptVariables(
    date=date(2026, 9, 6), username="agent", working_directory="/workspace"
)
SETTINGS = ModelSettings(endpoint="https://example.com/", deployment="gpt-5.4-mini")


def test_pinned_seed_and_manifest_are_reproducible(tmp_path: Path) -> None:
    seed = tmp_path / "seed"
    copytree(ROOT / "harnesses/seed", seed)
    first = prepare_baseline(seed, ROOT / "requirements.lock", SETTINGS, VARIABLES)
    second = prepare_baseline(seed, ROOT / "requirements.lock", SETTINGS, VARIABLES)
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
        prepare_baseline(seed, ROOT / "requirements.lock", SETTINGS, VARIABLES)


def test_settings_and_lock_change_identity(tmp_path: Path) -> None:
    seed = ROOT / "harnesses/seed"
    lock = tmp_path / "requirements.lock"
    lock.write_text("pydantic==2.11.7\n")
    first = prepare_baseline(seed, lock, SETTINGS, VARIABLES)
    lock.write_text("pydantic==2.11.8\n")
    assert (
        first.identity_hash
        != prepare_baseline(seed, lock, SETTINGS, VARIABLES).identity_hash
    )
    other = ModelSettings(
        endpoint="https://other.example.com/", deployment="gpt-5.4-mini"
    )
    assert (
        first.identity_hash
        != prepare_baseline(seed, lock, other, VARIABLES).identity_hash
    )


def test_missing_lock_is_not_a_valid_baseline(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        prepare_baseline(
            ROOT / "harnesses/seed", tmp_path / "missing", SETTINGS, VARIABLES
        )


def test_pinned_prompt_is_rendered_without_changing_policy() -> None:
    variables = PromptVariables(
        date=date(2026, 9, 6), username="agent", working_directory="/workspace"
    )
    manifest = prepare_baseline(
        ROOT / "harnesses/seed", ROOT / "requirements.lock", SETTINGS, variables
    )
    template = (ROOT / "harnesses/seed/systemprompt.md").read_text()
    assert manifest.prompt_variables == variables
    assert manifest.rendered_prompt == template.replace(
        "{{ date }}", "2026-09-06"
    ).replace("{{ username }}", "agent").replace(
        "{{ working_directory }}", "/workspace"
    )
    assert manifest.rendered_prompt.startswith(template.split("Date:")[0])


@pytest.mark.parametrize(
    "variables",
    [
        PromptVariables(
            date=date(2026, 9, 7), username="agent", working_directory="/workspace"
        ),
        PromptVariables(
            date=date(2026, 9, 6), username="other", working_directory="/workspace"
        ),
        PromptVariables(
            date=date(2026, 9, 6), username="agent", working_directory="/other"
        ),
    ],
)
def test_each_prompt_variable_changes_identity(variables: PromptVariables) -> None:
    original = prepare_baseline(
        ROOT / "harnesses/seed", ROOT / "requirements.lock", SETTINGS, VARIABLES
    )
    changed = prepare_baseline(
        ROOT / "harnesses/seed", ROOT / "requirements.lock", SETTINGS, variables
    )
    assert original.identity_hash != changed.identity_hash
    restored = type(original).model_validate_json(original.model_dump_json())
    assert restored.rendered_prompt == original.rendered_prompt
    assert restored.prompt_variables == VARIABLES


def test_evolved_workspace_can_add_a_skill_without_seed_pin(tmp_path: Path) -> None:
    from syndicate.models.baseline import bind_harness, prepare_workspace

    extra = tmp_path / "evolved"
    copytree(ROOT / "harnesses/seed", extra)
    skill = extra / "skills" / "itsm-mocks" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("PATCH seeded mock records.\n")
    evolved = prepare_workspace(extra, ROOT / "requirements.lock", SETTINGS, VARIABLES)
    assert any(item.path.endswith("SKILL.md") for item in evolved.artifacts)
    rebound = bind_harness(extra, ROOT / "requirements.lock", SETTINGS, VARIABLES)
    assert rebound.identity_hash == evolved.identity_hash


def test_prompt_suffix_changes_identity_without_mutating_seed() -> None:
    first = prepare_baseline(
        ROOT / "harnesses/seed", ROOT / "requirements.lock", SETTINGS, VARIABLES
    )
    second = prepare_baseline(
        ROOT / "harnesses/seed",
        ROOT / "requirements.lock",
        SETTINGS,
        VARIABLES,
        prompt_suffix="Fix the Okta user.",
    )
    assert first.prompt_suffix == ""
    assert first.identity_hash != second.identity_hash
    assert first.rendered_prompt in second.rendered_prompt
    assert "Fix the Okta user." in second.rendered_prompt


@pytest.mark.parametrize("username", ["", "agent\npolicy", "{{ date }}"])
def test_prompt_variables_reject_template_injection(username: str) -> None:
    with pytest.raises(ValueError):
        PromptVariables(
            date=date(2026, 9, 6), username=username, working_directory="/work"
        )
