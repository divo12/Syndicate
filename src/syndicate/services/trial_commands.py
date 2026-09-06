"""Simulated trial CLI and leftover run/judge helpers. Live execute uses Harbor."""

import argparse
import os

from syndicate.models.commands import JudgeTaskCommand, RunTrialCommand
from syndicate.models.envelope import ArtifactKind, CommandReceipt, CommandStatus
from syndicate.models.jobs import TaskOutcome, TaskResult
from syndicate.models.judging import ReportStatus, RunCoverage, TaskReport
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.services.executors import SimulatedExecutor


def run_trial_cli(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="trial")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--generation", required=True, type=int)
    parser.add_argument("--failing-task-id", default="")
    try:
        parsed = parser.parse_args(arguments)
    except SystemExit:
        return 2
    if not parsed.task_id.strip():
        return 2
    failing = parsed.failing_task_id.strip() or None
    print(
        SimulatedExecutor()
        .run((parsed.task_id,), parsed.generation, failing)[0]
        .model_dump_json()
    )
    return 0


def execute_run_trial(command: RunTrialCommand, store: ArtifactStore) -> CommandReceipt:
    generation = int(os.environ.get("SYNDICATE_GENERATION", "0"))
    result = SimulatedExecutor().run((command.task_id,), generation)[0]
    return CommandReceipt(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        status=CommandStatus.COMPLETED,
        artifact_refs=(store.write(command, ArtifactKind.RUN, result),),
    )


def execute_judge_task(
    command: JudgeTaskCommand, store: ArtifactStore
) -> CommandReceipt:
    result = _run_result(command, store)
    run_id = command.run_refs[0].operation_id
    report = TaskReport(
        task_id=command.task_id,
        judge_spec_hash=command.judge_spec_hash,
        run_ids=(run_id,),
        status=(
            ReportStatus.COMPLETE
            if result.outcome is TaskOutcome.PASSED
            else ReportStatus.INCOMPLETE
        ),
        coverage=(RunCoverage(run_id=run_id),),
    )
    return CommandReceipt(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        status=CommandStatus.COMPLETED,
        artifact_refs=(store.write(command, ArtifactKind.REPORT, report),),
    )


def _run_result(command: JudgeTaskCommand, store: ArtifactStore) -> TaskResult:
    if len(command.run_refs) != 1:
        raise ValueError("judge-task requires exactly one run reference")
    reference = command.run_refs[0]
    if reference.kind is not ArtifactKind.RUN:
        raise ValueError("judge-task run reference must be a run artifact")
    result = store.load(reference, TaskResult)
    if result.task_id != command.task_id:
        raise ValueError("run task_id does not match judge command")
    return result
