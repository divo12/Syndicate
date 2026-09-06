"""Trusted Harbor verifier classification without copying its payload locally."""

from enum import StrEnum
from typing import Protocol, Self
from uuid import UUID

from harbor.environments.base import BaseEnvironment
from harbor.models.task.task import Task
from harbor.models.trial.paths import TrialPaths
from harbor.models.verifier.result import VerifierResult
from harbor.verifier.verifier import Verifier
from pydantic import BaseModel, ConfigDict, Field, model_validator

from syndicate.harbor_agent import CleanupReceipt


class RunOutcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNVERIFIED = "unverified"
    CANCELLED = "cancelled"


class VerifierReason(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    MISSING_RESULT = "missing_result"
    UNSUPPORTED_REWARD = "unsupported_reward"
    VERIFIER_ERROR = "verifier_error"
    CLEANUP_INCOMPLETE = "cleanup_incomplete"


class VerifierReceipt(BaseModel):
    """A nonpayload receipt for the trusted verifier's terminal result."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    outcome: RunOutcome
    reason: VerifierReason
    reward: float | None = None
    raw_result_ref: str = Field(min_length=1)


class RunReceipt(BaseModel):
    """Terminal trusted outcome for a run; trajectory data stays in Neatlogs."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    operation_id: UUID
    attempt_id: UUID
    run_id: UUID
    task_id: str = Field(min_length=1)
    cleanup_complete: bool
    outcome: RunOutcome
    verifier: VerifierReceipt

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.verifier.outcome is not self.outcome:
            raise ValueError("Verifier outcome must match run outcome")
        if (
            self.outcome in (RunOutcome.PASS, RunOutcome.FAIL)
            and not self.cleanup_complete
        ):
            raise ValueError("Verified outcome requires complete cleanup")
        return self


class _VerifierRunner(Protocol):
    async def verify(self) -> VerifierResult: ...


def classify_verifier(result: VerifierResult, raw_result_ref: str) -> VerifierReceipt:
    """Only the original single `reward` field with 0/1 has benchmark meaning."""
    rewards = result.rewards
    if rewards is None:
        return VerifierReceipt(
            outcome=RunOutcome.UNVERIFIED,
            reason=VerifierReason.MISSING_RESULT,
            raw_result_ref=raw_result_ref,
        )
    if set(rewards) != {"reward"}:
        return VerifierReceipt(
            outcome=RunOutcome.UNVERIFIED,
            reason=VerifierReason.UNSUPPORTED_REWARD,
            raw_result_ref=raw_result_ref,
        )
    reward = rewards["reward"]
    if type(reward) not in (int, float) or reward not in (0, 1):
        return VerifierReceipt(
            outcome=RunOutcome.UNVERIFIED,
            reason=VerifierReason.UNSUPPORTED_REWARD,
            raw_result_ref=raw_result_ref,
        )
    value = float(reward)
    return VerifierReceipt(
        outcome=RunOutcome.PASS if value == 1 else RunOutcome.FAIL,
        reason=VerifierReason.PASSED if value == 1 else VerifierReason.FAILED,
        reward=value,
        raw_result_ref=raw_result_ref,
    )


async def verify_with_harbor(
    task: Task,
    paths: TrialPaths,
    environment: BaseEnvironment,
    raw_result_ref: str,
    cleanup: CleanupReceipt,
) -> VerifierReceipt:
    """Invoke Harbor's unmodified verifier after P08c cleanup succeeds."""
    if not cleanup.complete:
        return VerifierReceipt(
            outcome=RunOutcome.UNVERIFIED,
            reason=VerifierReason.CLEANUP_INCOMPLETE,
            raw_result_ref=raw_result_ref,
        )
    return await verify(Verifier(task, paths, environment), raw_result_ref)


async def verify(runner: _VerifierRunner, raw_result_ref: str) -> VerifierReceipt:
    try:
        return classify_verifier(await runner.verify(), raw_result_ref)
    except Exception:
        return VerifierReceipt(
            outcome=RunOutcome.UNVERIFIED,
            reason=VerifierReason.VERIFIER_ERROR,
            raw_result_ref=raw_result_ref,
        )
