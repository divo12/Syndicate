"""Operator preflight command and internal controller request transport."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from syndicate.controllers.pure_handlers import collect, compare, select
from syndicate.models.budget import ProductRole
from syndicate.models.commands import (
    CollectReportsCommand,
    CommandRequest,
    CompareHarnessCommand,
    JudgeTaskCommand,
    ProposeHarnessCommand,
    RunTrialCommand,
    SelectHarnessCommand,
    parse_command,
)
from syndicate.models.envelope import (
    ArtifactRef,
    Command,
    CommandError,
    CommandReceipt,
    CommandStatus,
    ErrorReason,
    PreflightCommand,
)
from syndicate.models.model_config import load_model_config
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.services.preflight import (
    AdmissionError,
    ControllerConfig,
    InfrastructureError,
    preflight,
    prepare_preflight,
)
from syndicate.services.schema_export import export_schemas

if TYPE_CHECKING:
    from syndicate.controllers.live_handlers import LiveHandlers


def contained(path: Path, root: Path) -> Path:
    if not path.is_absolute() or path.resolve() != path:
        raise ValueError("Absolute nonsymlink path required")
    if not path.is_relative_to(root):
        raise ValueError("Path outside controller root")
    return path


def read_request(arguments: list[str], root: Path) -> tuple[CommandRequest, Path]:
    if len(arguments) != 3 or arguments[:2] != ["execute", "--request"]:
        raise ValueError("Invalid invocation")
    path = contained(Path(arguments[2]), root / "runs")
    command = parse_command(path.read_bytes())
    expected = root / "runs" / str(command.operation_id) / str(command.attempt_id)
    if path != expected / "request.json":
        raise ValueError("Request IDs do not match controller path")
    return command, expected


def request_path(root: Path, command: Command) -> Path:
    contained(root, root)
    return (
        root
        / "runs"
        / str(command.operation_id)
        / str(command.attempt_id)
        / "request.json"
    )


def write_request(root: Path, command: Command) -> Path:
    path = request_path(root, command)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as request:
        request.write(command.model_dump_json())
    return path


def failure(
    status: CommandStatus, reason: ErrorReason, command: Command | None
) -> CommandReceipt:
    return CommandReceipt(
        operation_id=command.operation_id if command else None,
        attempt_id=command.attempt_id if command else None,
        status=status,
        error=CommandError(
            reason=reason,
            message={
                ErrorReason.INVALID_REQUEST: "Request validation or admission failed",
                ErrorReason.INVALID_CONFIGURATION: "Configuration validation failed",
                ErrorReason.INFRASTRUCTURE: "Runtime infrastructure failure",
            }[reason],
        ),
    )


def execute(command: PreflightCommand, run: Path, root: Path) -> CommandReceipt:
    anchor = contained(root / "controller.json", root)
    try:
        controller = ControllerConfig.model_validate_json(anchor.read_bytes())
    except ValidationError:
        raise InfrastructureError("Invalid controller declaration") from None
    return execute_preflight(command, run, controller)


def execute_preflight(
    command: PreflightCommand, run: Path, controller: ControllerConfig
) -> CommandReceipt:
    result = preflight(command, controller)
    payload = result.model_dump_json().encode()
    with (run / "preflight.json").open("xb") as artifact:
        artifact.write(payload)
    reference = ArtifactRef(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    return CommandReceipt(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        status=CommandStatus.COMPLETED,
        artifact_refs=(reference,),
    )


def operator_preflight(config_file: Path, root: Path) -> tuple[CommandReceipt, int]:
    """Provision one run without changing the shared controller trust anchor."""
    command = None
    try:
        command, controller = prepare_preflight(config_file)
        run = contained(
            root / "runs" / str(command.operation_id) / str(command.attempt_id), root
        )
        run.mkdir(parents=True, exist_ok=False)
        for name, model in (("request", command), ("controller", controller)):
            with (run / f"{name}.json").open("x", encoding="utf-8") as output:
                output.write(model.model_dump_json())
        return execute_preflight(command, run, controller), 0
    except (OSError, InfrastructureError):
        return failure(CommandStatus.FAILED, ErrorReason.INFRASTRUCTURE, command), 1
    except ValueError:
        return failure(
            CommandStatus.BLOCKED, ErrorReason.INVALID_CONFIGURATION, command
        ), 2


def execute_pure(
    command: CommandRequest, root: Path, handlers: LiveHandlers | None = None
) -> CommandReceipt:
    controller = ControllerConfig.model_validate_json(
        (root / "controller.json").read_bytes()
    )
    if command.content_hash not in controller.approved_request_hashes:
        raise ValueError("Controller did not approve this command request")
    store = ArtifactStore(root)
    if isinstance(command, RunTrialCommand):
        from syndicate.controllers.live_handlers import LiveHandlers, run

        _admit_budget(command, controller, ProductRole.EXECUTOR)
        return run(
            command,
            store,
            load_model_config(controller.env_file).api_key,
            handlers or LiveHandlers(),
            controller.benchmark_root,
            controller.assignments,
        )
    if isinstance(command, JudgeTaskCommand):
        from syndicate.controllers.live_handlers import LiveHandlers, judge

        _admit_budget(command, controller, ProductRole.TASK_JUDGE)
        return judge(
            command,
            store,
            load_model_config(controller.env_file).api_key,
            handlers or LiveHandlers(),
        )
    if isinstance(command, ProposeHarnessCommand):
        from syndicate.controllers.live_handlers import LiveHandlers, propose

        _admit_budget(command, controller, ProductRole.IMPROVEMENT_AGENT)
        return propose(
            command,
            store,
            load_model_config(controller.env_file).api_key,
            handlers or LiveHandlers(),
        )
    if isinstance(command, CollectReportsCommand):
        return collect(command, store)
    if isinstance(command, CompareHarnessCommand):
        return compare(command, store)
    if isinstance(command, SelectHarnessCommand):
        return select(command, store)
    raise ValueError("Unknown controller command")


def _admit_budget(
    command: RunTrialCommand | JudgeTaskCommand | ProposeHarnessCommand,
    controller: ControllerConfig,
    role: ProductRole,
) -> None:
    if not command.budget.fits_within(controller.budget.budget_for(role)):
        raise ValueError("Command budget exceeds approved role cap")


def dispatch(arguments: list[str]) -> tuple[CommandReceipt, int]:
    try:
        root = Path.cwd().resolve() / ".syndicate"
    except OSError:
        return failure(CommandStatus.FAILED, ErrorReason.INFRASTRUCTURE, None), 1
    if len(arguments) == 3 and arguments[:2] == ["preflight", "--config"]:
        return operator_preflight(Path(arguments[2]), root)
    try:
        command, run = read_request(arguments, root)
    except (OSError, ValueError):
        return failure(CommandStatus.FAILED, ErrorReason.INVALID_REQUEST, None), 2
    try:
        if isinstance(command, PreflightCommand):
            return execute(command, run, root), 0
        return execute_pure(command, root), 0
    except AdmissionError:
        return failure(CommandStatus.FAILED, ErrorReason.INVALID_REQUEST, command), 2
    except (OSError, InfrastructureError):
        return failure(CommandStatus.FAILED, ErrorReason.INFRASTRUCTURE, command), 1
    except ValueError:
        return failure(
            CommandStatus.BLOCKED, ErrorReason.INVALID_CONFIGURATION, command
        ), 0


def main(arguments: list[str]) -> int:
    if arguments == ["--help"]:
        print("Usage: python -m syndicate.cli preflight --config CAMPAIGN_JSON")
        print("Internal transport: execute --request ABSOLUTE_REQUEST_JSON")
        return 0
    if arguments == ["export-schema"]:
        root = Path.cwd().resolve() / ".syndicate"
        print(export_schemas(root).model_dump_json())
        return 0
    receipt, exit_code = dispatch(arguments)
    if receipt.error:
        print(receipt.error.reason.value, file=sys.stderr)
    print(receipt.model_dump_json())
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
