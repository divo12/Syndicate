from pathlib import Path
from uuid import UUID

import pytest

from syndicate.models.envelope import ArtifactKind, PreflightCommand
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.services.preflight import PreflightResult


def command() -> PreflightCommand:
    return PreflightCommand(
        operation_id=UUID(int=1), attempt_id=UUID(int=2), manifest_hash="a" * 64
    )


def result() -> PreflightResult:
    return PreflightResult(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        configuration_valid=True,
        live_model_verified=False,
        manifest_hash="a" * 64,
        model_settings_hash="b" * 64,
    )


def test_store_writes_and_hash_validates_a_fixed_kind_path(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path.resolve())
    reference = store.write(command(), ArtifactKind.PREFLIGHT, result())
    assert store.load(reference, PreflightResult) == result()
    assert store.path_for(reference).name == "preflight.json"


def test_store_rejects_tampered_or_symlinked_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path.resolve())
    reference = store.write(command(), ArtifactKind.PREFLIGHT, result())
    path = store.path_for(reference)
    path.write_text("tampered")
    with pytest.raises(ValueError, match="hash"):
        store.load(reference, PreflightResult)
