from pathlib import Path

import pytest
from pydantic import ValidationError

from syndicate.models.model_config import (
    ApiFamily,
    ModelConfigError,
    ModelSettings,
    load_model_config,
)


def env_file(
    tmp_path: Path, *, deployment: str = "gpt-5.4-mini", key: str = "test-secret"
) -> Path:
    path = tmp_path / ".env"
    path.write_text(
        f'AZURE_OPENAI_API_KEY="{key}"\n'
        "AZURE_OPENAI_BASE_URL=https://example.openai.azure.com/openai/v1/\n"
        f"AZURE_OPENAI_DEPLOYMENT={deployment}\n"
        "UNRELATED=$(never-execute)\n",
    )
    return path


def test_load_is_immutable_and_secret_safe(tmp_path: Path) -> None:
    config = load_model_config(env_file(tmp_path))
    assert config.settings.requested_model == "gpt-5.4-mini"
    assert config.settings.api_family is ApiFamily.RESPONSES
    assert config.api_key.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(config)
    assert "test-secret" not in config.model_dump_json()
    assert "api_key" not in config.model_dump_json()
    with pytest.raises(ValidationError):
        config.settings.deployment = "gpt-5.4-mini"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        config.settings = config.settings  # type: ignore[misc]


def test_identity_is_canonical_and_credential_independent(tmp_path: Path) -> None:
    first = load_model_config(env_file(tmp_path))
    second = load_model_config(env_file(tmp_path, key="rotated-secret"))
    assert first.settings.canonical_json() == second.settings.canonical_json()
    assert first.settings.settings_hash == second.settings.settings_hash
    assert len(first.settings.settings_hash) == 64
    changed = ModelSettings(
        endpoint="https://other.openai.azure.com/", deployment="gpt-5.4-mini"
    )
    assert changed.settings_hash != first.settings.settings_hash


@pytest.mark.parametrize("deployment", ["alias", "gpt-4o", ""])
def test_reject_unverified_deployment(tmp_path: Path, deployment: str) -> None:
    with pytest.raises(ModelConfigError, match="Invalid model configuration"):
        load_model_config(env_file(tmp_path, deployment=deployment))


@pytest.mark.parametrize(
    "key", ["", " ", "${HOST_SECRET}", "`command`", 'unterminated"quote', 'one" two']
)
def test_reject_empty_or_interpolated_key(tmp_path: Path, key: str) -> None:
    with pytest.raises(ModelConfigError):
        load_model_config(env_file(tmp_path, key=key))


@pytest.mark.parametrize(
    "bad_line",
    [
        "AZURE_OPENAI_API_KEY",
        'AZURE_OPENAI_API_KEY="unterminated',
        "AZURE_OPENAI_API_KEY=one two",
        "AZURE_OPENAI_API_KEY=duplicate-secret",
    ],
)
def test_reject_malformed_or_duplicate_selected_values(
    tmp_path: Path, bad_line: str
) -> None:
    path = env_file(tmp_path)
    path.write_text(path.read_text() + bad_line + "\n")
    with pytest.raises(ModelConfigError) as caught:
        load_model_config(path)
    assert "secret" not in str(caught.value)


def test_no_ambient_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "ambient-secret")
    path = env_file(tmp_path)
    path.write_text("\n".join(path.read_text().splitlines()[1:]))
    with pytest.raises(ModelConfigError):
        load_model_config(path)
    with pytest.raises(ModelConfigError):
        load_model_config(tmp_path / "missing")


def test_reject_assignment_without_equals(tmp_path: Path) -> None:
    path = env_file(tmp_path)
    path.write_text(
        path.read_text().replace(
            'AZURE_OPENAI_API_KEY="test-secret"', "AZURE_OPENAI_API_KEY"
        )
    )
    with pytest.raises(ModelConfigError):
        load_model_config(path)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com",
        "https://user:password@example.com",
        "https://:password@example.com",
        "https://@example.com",
        "https:///missing-host",
        "https://example.com:65536/",
        "https://example.com/white space",
        "https://example.com/?key=secret",
        "https://example.com/#secret",
        "not-a-url",
        "https://example.com:invalid/",
        "https://example.com:0/",
    ],
)
def test_reject_unsafe_endpoint(tmp_path: Path, endpoint: str) -> None:
    path = env_file(tmp_path)
    path.write_text(
        path.read_text().replace(
            "https://example.openai.azure.com/openai/v1/",
            f'"{endpoint}"',
        )
    )
    with pytest.raises(ModelConfigError):
        load_model_config(path)


def test_reject_unsupported_settings() -> None:
    for extra in ("temperature", "fallback_model", "api_version"):
        with pytest.raises(ValidationError):
            ModelSettings.model_validate(
                {
                    "endpoint": "https://example.com/",
                    "deployment": "gpt-5.4-mini",
                    extra: "unsupported",
                }
            )
    with pytest.raises(ValidationError):
        ModelSettings.model_validate(
            {
                "endpoint": "https://example.com/",
                "deployment": "gpt-5.4-mini",
                "requested_model": "gpt-4o",
            }
        )
    with pytest.raises(ValidationError):
        ModelSettings.model_validate(
            {
                "endpoint": "https://example.com/",
                "deployment": "gpt-5.4-mini",
                "api_family": "chat_completions",
            }
        )


def test_export_quotes_comments_and_no_environment_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "ambient-secret")
    path = env_file(tmp_path)
    path.write_text(
        path.read_text().replace(
            'AZURE_OPENAI_API_KEY="test-secret"',
            'export AZURE_OPENAI_API_KEY="quoted # secret" # comment',
        )
    )
    config = load_model_config(path)
    assert config.api_key.get_secret_value() == "quoted # secret"
    assert os.environ["AZURE_OPENAI_API_KEY"] == "ambient-secret"


def test_error_traceback_does_not_expose_values(tmp_path: Path) -> None:
    import traceback

    path = env_file(tmp_path, deployment="secret-invalid-model")
    with pytest.raises(ModelConfigError) as caught:
        load_model_config(path)
    rendered = "".join(traceback.format_exception(caught.value))
    assert "secret-invalid-model" not in rendered
    assert "test-secret" not in rendered
    assert str(path) not in str(caught.value)
