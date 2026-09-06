from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from syndicate.controllers.preflight import execute_pure
from syndicate.models.commands import CollectReportsCommand
from syndicate.models.envelope import (
    ArtifactKind,
    ArtifactRef,
    CommandReceipt,
    CommandStatus,
)


def _ref(kind: ArtifactKind) -> ArtifactRef:
    return ArtifactRef(
        kind=kind,
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        sha256="a" * 64,
    )


def test_execute_pure_dispatches_collect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = CollectReportsCommand(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        expected_reports_ref=_ref(ArtifactKind.EXPECTED_REPORTS),
        report_refs=(_ref(ArtifactKind.REPORT),),
    )
    written = CommandReceipt(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        status=CommandStatus.COMPLETED,
        artifact_refs=(_ref(ArtifactKind.COLLECTION),),
    )

    def fake_collect(received: CollectReportsCommand, store: object) -> CommandReceipt:
        assert received == command
        return written

    monkeypatch.setattr(
        "syndicate.controllers.preflight.ControllerConfig.model_validate_json",
        lambda _: SimpleNamespace(approved_request_hashes=(command.content_hash,)),
    )
    monkeypatch.setattr(
        "syndicate.controllers.preflight.ArtifactStore",
        lambda root: SimpleNamespace(root=root),
    )
    monkeypatch.setattr("syndicate.controllers.preflight.collect", fake_collect)
    root = tmp_path / ".syndicate"
    root.mkdir()
    (root / "controller.json").write_text("{}")
    assert execute_pure(command, root) == written
