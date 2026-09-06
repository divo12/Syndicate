"""Offline Azure model configuration; deployment verification belongs to M1."""

import hashlib
import json
import shlex
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import SplitResult, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
)


class Provider(StrEnum):
    AZURE_OPENAI = "azure_openai"


class ApiFamily(StrEnum):
    RESPONSES = "responses"


class ModelConfigError(ValueError):
    """Safe configuration failure, containing neither source values nor paths."""


def _validate_https_authority(parsed: SplitResult) -> None:
    """Require an HTTPS host with a valid port and no embedded credentials."""
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.port == 0
        or parsed.username is not None
    ):
        raise ValueError("Expected a nonsecret HTTPS endpoint")


class ModelSettings(BaseModel):
    """Nonsecret campaign identity, shared by every product role.

    Only the canonical deployment is accepted until M1 records alias verification.
    No sampling parameters or provider limits are claimed verified by this module.
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )

    provider: Provider = Provider.AZURE_OPENAI
    endpoint: str
    api_family: ApiFamily = ApiFamily.RESPONSES
    deployment: Literal["gpt-5.4-mini"]
    requested_model: Literal["gpt-5.4-mini"] = "gpt-5.4-mini"

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        _validate_https_authority(parsed)
        if (
            parsed.query
            or parsed.fragment
            or any(character.isspace() for character in value)
        ):
            raise ValueError("Expected a nonsecret HTTPS endpoint")
        return value

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )

    @property
    def settings_hash(self) -> str:
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()


class ModelConfig(BaseModel):
    """Credential-bearing runtime input; serialization excludes the API key."""

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, hide_input_in_errors=True
    )

    settings: ModelSettings
    api_key: SecretStr = Field(exclude=True, repr=False)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("Missing API credential")
        return value


_ENV_NAMES = frozenset(
    {
        "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_BASE_URL",
        "AZURE_OPENAI_DEPLOYMENT",
    }
)


def _parse_env_value(raw: str) -> str:
    """Decode one literal shell value, rejecting interpolation and empty values."""
    if "$" in raw or "`" in raw:
        raise ModelConfigError("Invalid environment assignment")
    tokens = shlex.split(raw, comments=True, posix=True)
    if len(tokens) != 1 or not tokens[0].strip():
        raise ModelConfigError("Missing or malformed environment value")
    return tokens[0]


def _read_env(path: Path) -> dict[str, str]:
    """Parse only selected assignments; never execute, interpolate or mutate env."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, raw = line.strip().removeprefix("export ").partition("=")
        name = name.strip()
        if name not in _ENV_NAMES:
            continue
        if name in values:
            raise ModelConfigError("Invalid environment assignment")
        values[name] = _parse_env_value(raw)
    if values.keys() != _ENV_NAMES:
        raise ModelConfigError("Missing required Azure configuration")
    return values


def load_model_config(env_file: Path) -> ModelConfig:
    """Load an explicit file without ambient fallback or any provider request.

    Supported dotenv syntax: KEY=value, optional export, shell quotes and comments.
    Interpolation, duplicate selected keys and empty selected values are rejected.
    Unrelated assignments are ignored. Errors never expose file contents or paths.
    """
    try:
        values = _read_env(env_file)
        settings = ModelSettings.model_validate(
            {
                "endpoint": values["AZURE_OPENAI_BASE_URL"],
                "deployment": values["AZURE_OPENAI_DEPLOYMENT"],
            }
        )
        return ModelConfig(
            settings=settings, api_key=SecretStr(values["AZURE_OPENAI_API_KEY"])
        )
    except (OSError, ValueError):
        raise ModelConfigError("Invalid model configuration") from None
