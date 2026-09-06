import asyncio
from uuid import uuid4

import pytest
from harbor.models.verifier.result import VerifierResult

from syndicate.benchmark import (
    RunOutcome,
    RunReceipt,
    VerifierReason,
    VerifierReceipt,
    classify_verifier,
    verify,
)


@pytest.mark.parametrize(
    ("rewards", "outcome", "reason"),
    [
        ({"reward": 1}, RunOutcome.PASS, VerifierReason.PASSED),
        ({"reward": 0}, RunOutcome.FAIL, VerifierReason.FAILED),
        (None, RunOutcome.UNVERIFIED, VerifierReason.MISSING_RESULT),
        ({"reward": 0.5}, RunOutcome.UNVERIFIED, VerifierReason.UNSUPPORTED_REWARD),
        ({"other": 1}, RunOutcome.UNVERIFIED, VerifierReason.UNSUPPORTED_REWARD),
    ],
)
def test_only_supported_harbor_rewards_are_verified(
    rewards: dict[str, float] | None, outcome: RunOutcome, reason: VerifierReason
) -> None:
    receipt = classify_verifier(VerifierResult(rewards=rewards), "harbor:opaque")
    assert (receipt.outcome, receipt.reason) == (outcome, reason)
    assert receipt.raw_result_ref == "harbor:opaque"


class BrokenVerifier:
    async def verify(self) -> VerifierResult:
        raise RuntimeError("Harbor unavailable")


def test_verifier_exception_is_unverified() -> None:
    receipt = asyncio.run(verify(BrokenVerifier(), "harbor:opaque"))
    assert receipt.outcome is RunOutcome.UNVERIFIED
    assert receipt.reason is VerifierReason.VERIFIER_ERROR


def test_run_receipt_rejects_verifier_outcome_mismatch() -> None:
    verifier = VerifierReceipt(
        outcome=RunOutcome.PASS,
        reason=VerifierReason.PASSED,
        reward=1.0,
        raw_result_ref="harbor:opaque",
    )
    with pytest.raises(ValueError, match="outcome"):
        RunReceipt(
            operation_id=uuid4(),
            attempt_id=uuid4(),
            run_id=uuid4(),
            task_id="task-a-1",
            cleanup_complete=True,
            outcome=RunOutcome.FAIL,
            verifier=verifier,
        )


def test_run_receipt_blocks_verified_outcome_without_cleanup() -> None:
    verifier = VerifierReceipt(
        outcome=RunOutcome.FAIL,
        reason=VerifierReason.FAILED,
        reward=0.0,
        raw_result_ref="harbor:opaque",
    )
    with pytest.raises(ValueError, match="cleanup"):
        RunReceipt(
            operation_id=uuid4(),
            attempt_id=uuid4(),
            run_id=uuid4(),
            task_id="task-a-1",
            cleanup_complete=False,
            outcome=RunOutcome.FAIL,
            verifier=verifier,
        )
