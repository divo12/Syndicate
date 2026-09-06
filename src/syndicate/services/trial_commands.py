"""Installed run-trial and judge-task handlers used by Trigger invokePython."""

import argparse
import os

from syndicate.models.commands import JudgeTaskCommand, RunTrialCommand
from syndicate.models.envelope import ArtifactKind, CommandReceipt, CommandStatus
from syndicate.models.jobs import TaskResult
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.services.executors import SimulatedExecutor


def run_trial_cli(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="trial")
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--generation", required=True, type=int)
    try:
        parsed = parser.parse_args(arguments)
    except SystemExit:
        return 2
    print(
        SimulatedExecutor()
        .run((parsed.task_id,), parsed.generation)[0]
        .model_dump_json()
    )
    return 0


def execute_run_trial(command: RunTrialCommand, store: ArtifactStore) -> CommandReceipt:
    generation = int(os.environ.get("SYNDICATE_GENERATION", "0"))
    result = SimulatedExecutor().run((command.task_id,), generation)[0]
    return _receipt(command, store, ArtifactKind.RUN, result)


def execute_judge_task(
    command: JudgeTaskCommand, store: ArtifactStore
) -> CommandReceipt:
    result = store.load(command.run_refs[0], TaskResult)
    return _receipt(command, store, ArtifactKind.REPORT, result)


def _receipt(
    command: RunTrialCommand | JudgeTaskCommand,
    store: ArtifactStore,
    kind: ArtifactKind,
    result: TaskResult,
) -> CommandReceipt:
    return CommandReceipt(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        status=CommandStatus.COMPLETED,
        artifact_refs=(store.write(command, kind, result),),
    )
