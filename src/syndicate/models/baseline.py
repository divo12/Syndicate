"""Pin exact AHE seed inputs; preparation does not certify runnable H0."""

import datetime
import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from syndicate.models.model_config import ModelSettings


class SeedArtifact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: str
    sha256: str


SEED_ARTIFACTS = (
    SeedArtifact(
        path="systemprompt.md",
        sha256="081f513b5db4892ead447fc4dfe38d1e534b7a430e27f63c35c7e5ffad1ce41d",
    ),
    SeedArtifact(
        path="LongTermMEMORY.md",
        sha256="025393be092bd363cde00fed5521da87293af61b9ea373fa77851606efd07f3e",
    ),
    SeedArtifact(
        path="ShortTermMEMORY.md",
        sha256="00c2a307956f5d26e0d8c4fb86061a8d65a84cafef11a75e0026db93ae5467c4",
    ),
    SeedArtifact(
        path="code_agent.yaml",
        sha256="792be006108f7a7706cc893ab1280d1ddeec6efc13a8a310a94cce58a4c600cf",
    ),
    SeedArtifact(
        path="tool_descriptions/run_shell_command.tool.yaml",
        sha256="833e909437a583c86cfedf80bb75534a1ecd2e7baeb2c1d37c4f02556530f0aa",
    ),
    SeedArtifact(
        path="LICENSE",
        sha256="d1404dcdb97e84a6f990ed3d205226d481d2bbb857b13f00585c3b863e217d35",
    ),
)


class BaselineStage(StrEnum):
    PREPARED = "prepared"


class PromptVariables(BaseModel):
    """Controller-pinned pair inputs; never resolve these from host state."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    date: datetime.date
    username: str = Field(pattern=r"^[^\s{}]+$")
    working_directory: str = Field(pattern=r"^/[^\r\n{}]*$")

    def render(self, template: str) -> str:
        return (
            template.replace("{{ date }}", self.date.isoformat())
            .replace("{{ username }}", self.username)
            .replace("{{ working_directory }}", self.working_directory)
        )


class BaselineManifest(BaseModel):
    """Preparation receipt, not verified H0."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    stage: BaselineStage = BaselineStage.PREPARED
    ahe_revision: Literal["8b2a55d97590363fe50c3cc6b5e833b020a4bb4c"] = (
        "8b2a55d97590363fe50c3cc6b5e833b020a4bb4c"
    )
    nexau_revision: Literal["35ee1861546db3cb280a6e17e38a74060d7c96c3"] = (
        "35ee1861546db3cb280a6e17e38a74060d7c96c3"
    )
    upstream_shell_sha256: str = (
        "e35a4992ea17816a0442eba6dbabb537309101a9ad5757922da945a513ee69e7"
    )
    artifacts: tuple[SeedArtifact, ...]
    compatibility_diff: Literal[""] = ""
    framework_lock_sha256: str
    model: ModelSettings
    prompt_variables: PromptVariables
    rendered_prompt: str

    @property
    def identity_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()


def prepare_baseline(
    seed_dir: Path,
    framework_lock: Path,
    model: ModelSettings,
    prompt_variables: PromptVariables,
) -> BaselineManifest:
    """Verify vendored bytes and bind their identity to model settings and lock bytes.

    The original YAML is provenance only: do not execute its unresolved LLM env
    settings or unverified request limits. Runtime adaptation is a separate diff.
    """
    paths = tuple(seed_dir.rglob("*"))
    if seed_dir.is_symlink() or any(path.is_symlink() for path in paths):
        raise ValueError("seed must not contain symlinks")
    actual = {path.relative_to(seed_dir).as_posix() for path in paths if path.is_file()}
    if actual != {artifact.path for artifact in SEED_ARTIFACTS}:
        raise ValueError("seed file set differs from pinned upstream")
    for artifact in SEED_ARTIFACTS:
        digest = hashlib.sha256((seed_dir / artifact.path).read_bytes()).hexdigest()
        if digest != artifact.sha256:
            raise ValueError(f"seed artifact differs from upstream: {artifact.path}")
    return BaselineManifest(
        artifacts=SEED_ARTIFACTS,
        framework_lock_sha256=hashlib.sha256(framework_lock.read_bytes()).hexdigest(),
        model=model,
        prompt_variables=prompt_variables,
        rendered_prompt=prompt_variables.render(
            (seed_dir / "systemprompt.md").read_text(encoding="utf-8")
        ),
    )
