"""Pinned-cwd controller transport: execute --request ABSOLUTE_REQUEST_JSON."""

import hashlib
import sys
from pathlib import Path

from syndicate.cli_envelope import (
    ArtifactKind,
    ArtifactRef,
    Command,
    CommandError,
    CommandReceipt,
    CommandRequest,
    CommandStatus,
    ErrorReason,
    PreflightCommand,
    parse_command,
)
from syndicate.preflight import AdmissionError, ControllerConfig, preflight


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


def failure(
    status: CommandStatus, reason: ErrorReason, command: Command | None
) -> CommandReceipt:
    return CommandReceipt(
        operation_id=command.operation_id if command else None,
        attempt_id=command.attempt_id if command else None,
        status=status,
        error=CommandError(reason=reason, message="Controller input validation failed"),
    )


def execute(command: PreflightCommand, run: Path, root: Path) -> CommandReceipt:
    anchor = contained(root / "controller.json", root)
    controller = ControllerConfig.model_validate_json(anchor.read_bytes())
    result = preflight(command, controller)
    payload = result.model_dump_json().encode()
    with (run / "preflight.json").open("xb") as artifact:
        artifact.write(payload)
    reference = ArtifactRef(
        kind=ArtifactKind.PREFLIGHT,
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


def dispatch(arguments: list[str]) -> tuple[CommandReceipt, int]:
    root = Path.cwd().resolve() / ".syndicate"
    try:
        command, run = read_request(arguments, root)
    except (OSError, ValueError):
        return failure(CommandStatus.FAILED, ErrorReason.INVALID_REQUEST, None), 2
    try:
        if not isinstance(command, PreflightCommand):
            raise ValueError("Command handler is not installed")
        return execute(command, run, root), 0
    except AdmissionError:
        return failure(CommandStatus.FAILED, ErrorReason.INVALID_REQUEST, command), 2
    except ValueError:
        return failure(
            CommandStatus.BLOCKED, ErrorReason.INVALID_CONFIGURATION, command
        ), 0
    except OSError:
        return failure(CommandStatus.FAILED, ErrorReason.INFRASTRUCTURE, command), 1


def main(arguments: list[str]) -> int:
    receipt, exit_code = dispatch(arguments)
    if receipt.error:
        print(receipt.error.reason.value, file=sys.stderr)
    print(receipt.model_dump_json())
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
