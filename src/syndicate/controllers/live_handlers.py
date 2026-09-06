"""Thin controller adapters for runtime, judging, and improvement services."""

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openai import OpenAI
from pydantic import SecretStr

from syndicate.controllers.handler_inputs import JudgeInput, ProposalInput, RuntimeInput
from syndicate.models.budget import ProductRole
from syndicate.models.candidate import CandidateWorkspace
from syndicate.models.commands import (
    JudgeTaskCommand,
    ProposeHarnessCommand,
    RunTrialCommand,
)
from syndicate.models.envelope import (
    ArtifactKind,
    ArtifactRef,
    CommandReceipt,
    CommandStatus,
)
from syndicate.models.improvement import (
    CandidateReceipt,
    FailureDiagnosis,
)
from syndicate.models.judging import TaskReport
from syndicate.models.runtime import RuntimeRequest
from syndicate.observability.neatlogs_readback import NeatlogsReadbackReader
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.services.benchmark import RunReceipt
from syndicate.services.candidate import create_candidate_workspace
from syndicate.services.evidence import EvidenceReader
from syndicate.services.improvement import apply_proposal
from syndicate.services.judge_dispatch import dispatch_judge
from syndicate.services.runtime import dispatch_role


class TrialRunner(Protocol):
    async def __call__(self, request: RuntimeRequest, key: SecretStr) -> RunReceipt: ...


class JudgeRunner(Protocol):
    async def __call__(
        self, input: JudgeInput, runs: tuple[RunReceipt, ...], key: SecretStr
    ) -> TaskReport: ...


class ProposalRunner(Protocol):
    def __call__(self, input: ProposalInput, key: SecretStr) -> str: ...


class CheckRunner(Protocol):
    def __call__(self, workspace: CandidateWorkspace, command: str) -> bool: ...


async def _run_trial(request: RuntimeRequest, key: SecretStr) -> RunReceipt:
    """The stock Harbor lifecycle has no controller entry point on this branch."""
    del request, key
    raise RuntimeError("Harbor trial environment is not attached to this controller")


async def _judge_task(
    input: JudgeInput, runs: tuple[RunReceipt, ...], key: SecretStr
) -> TaskReport:
    remote = NeatlogsReadbackReader(key)
    reader = EvidenceReader(remote, input.evidence_grants, input.run_grants, runs)
    try:
        with OpenAI(
            api_key=key.get_secret_value(),
            base_url=input.request.model.endpoint,
            max_retries=0,
            timeout=input.request.budget.max_seconds,
        ) as client:
            return await dispatch_judge(
                input.spec,
                reader,
                input.verifier_refs,
                input.request,
                client,
            )
    finally:
        remote.close()


def _propose(input: ProposalInput, key: SecretStr) -> str:
    request = input.role_request
    with OpenAI(
        api_key=key.get_secret_value(),
        base_url=request.model.endpoint,
        max_retries=0,
        timeout=request.budget.max_seconds,
    ) as client:
        return asyncio.run(dispatch_role(request, (), client)).final_text


def _run_check(workspace: CandidateWorkspace, command: str) -> bool:
    del workspace, command
    raise RuntimeError("Candidate check sandbox is not attached to this controller")


@dataclass(frozen=True, slots=True)
class LiveHandlers:
    trial: TrialRunner = _run_trial
    judge: JudgeRunner = _judge_task
    proposal: ProposalRunner = _propose
    check: CheckRunner = _run_check


def _receipt(
    command: RunTrialCommand | JudgeTaskCommand | ProposeHarnessCommand,
    ref: ArtifactRef,
) -> CommandReceipt:
    return CommandReceipt(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        status=CommandStatus.COMPLETED,
        artifact_refs=(ref,),
    )


def run(
    command: RunTrialCommand,
    store: ArtifactStore,
    key: SecretStr,
    handlers: LiveHandlers,
) -> CommandReceipt:
    input = store.load(command.runtime_request_ref, RuntimeInput)
    if input.request.budget != command.budget:
        raise ValueError("Runtime request budget differs from command")
    try:
        result = asyncio.run(handlers.trial(input.request, key))
    except (OSError, RuntimeError, TimeoutError) as error:
        raise ValueError("Harbor trial did not complete") from error
    if result.task_id != command.task_id:
        raise ValueError("Run receipt task does not match command")
    return _receipt(command, store.write(command, ArtifactKind.RUN, result))


def judge(
    command: JudgeTaskCommand,
    store: ArtifactStore,
    key: SecretStr,
    handlers: LiveHandlers,
) -> CommandReceipt:
    input = store.load(command.judge_input_ref, JudgeInput)
    runs = tuple(store.load(reference, RunReceipt) for reference in command.run_refs)
    if (
        input.spec.task_id != command.task_id
        or input.spec.spec_hash != command.judge_spec_hash
        or input.request.role is not ProductRole.TASK_JUDGE
        or input.request.budget != command.budget
        or any(receipt.task_id != command.task_id for receipt in runs)
    ):
        raise ValueError("Judge inputs do not match command")
    try:
        result = asyncio.run(handlers.judge(input, runs, key))
    except (OSError, RuntimeError, TimeoutError) as error:
        raise ValueError("Judge dispatch did not complete") from error
    if result.task_id != command.task_id:
        raise ValueError("Judge report task does not match command")
    return _receipt(command, store.write(command, ArtifactKind.REPORT, result))


def propose(
    command: ProposeHarnessCommand,
    store: ArtifactStore,
    key: SecretStr,
    handlers: LiveHandlers,
) -> CommandReceipt:
    diagnosis = store.load(command.diagnosis_ref, FailureDiagnosis)
    input = store.load(command.proposal_input_ref, ProposalInput)
    if (
        input.request.diagnosis != diagnosis
        or input.request.candidate_id != command.candidate_id
        or diagnosis.parent_harness_hash != "sha256:" + command.parent_harness_hash
        or input.role_request.role is not ProductRole.IMPROVEMENT_AGENT
        or input.role_request.budget != command.budget
    ):
        raise ValueError("Proposal inputs do not match command")
    check = handlers.check
    source = Path(__file__).parents[3] / "harnesses" / "seed"
    workspace = create_candidate_workspace(
        source, diagnosis.edit_scope.allowed_paths, store.root / "candidates"
    )
    try:
        result: CandidateReceipt = apply_proposal(
            input.request,
            workspace,
            lambda _: handlers.proposal(input, key),
            check,
        )
    except (OSError, RuntimeError, TimeoutError) as error:
        raise ValueError("Improvement dispatch did not complete") from error
    reference = store.write(command, ArtifactKind.CANDIDATE_RECEIPT, result)
    return _receipt(command, reference)
