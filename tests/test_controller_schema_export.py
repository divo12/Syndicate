from pathlib import Path
from uuid import UUID

from syndicate.cli import request_path, write_request
from syndicate.models.budget import BudgetCap
from syndicate.models.commands import RunTrialCommand
from syndicate.models.envelope import ArtifactKind, ArtifactRef
from syndicate.services.schema_export import schema_artifact_paths, write_schemas


def command() -> RunTrialCommand:
    return RunTrialCommand(
        operation_id=UUID(int=1),
        attempt_id=UUID(int=2),
        task_id="task-a-1",
        harness_hash="a" * 64,
        memory_hash="b" * 64,
        model_config_hash="c" * 64,
        runtime_image_hash="d" * 64,
        judge_spec_hash="e" * 64,
        verifier_version="v1",
        runtime_request_ref=ArtifactRef(
            kind=ArtifactKind.RUNTIME_REQUEST,
            operation_id=UUID(int=3),
            attempt_id=UUID(int=4),
            sha256="f" * 64,
        ),
        budget=BudgetCap(max_tokens=1, max_seconds=1, max_spend_microusd=1),
    )


def test_controller_owns_versioned_schema_and_request_paths(tmp_path: Path) -> None:
    root = (tmp_path / ".syndicate").resolve()
    paths = write_schemas(root)
    request = write_request(root, command())
    assert paths == schema_artifact_paths(root)
    assert paths[0].name == "command-request-v1.json"
    assert paths[1].name == "command-receipt-v1.json"
    assert request == request_path(root, command())
    assert request.is_absolute()
    assert request.read_text() == command().model_dump_json()
