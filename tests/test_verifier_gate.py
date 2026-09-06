"""The first verifier PR must require successful cleanup before invoking Harbor."""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest
from harbor.environments.base import BaseEnvironment
from harbor.models.task.task import Task
from harbor.models.trial.paths import TrialPaths
from harbor.models.verifier.result import VerifierResult

from syndicate.benchmark import RunOutcome, verify_with_harbor
from syndicate.harbor_agent import CleanupReceipt


@pytest.mark.parametrize("complete", [False, True])
def test_verifier_requires_cleanup_proof(complete: bool) -> None:
    task, paths, environment = (
        Mock(spec=Task),
        Mock(spec=TrialPaths),
        Mock(spec=BaseEnvironment),
    )
    with patch("syndicate.benchmark.Verifier") as factory:
        factory.return_value.verify = AsyncMock(
            return_value=VerifierResult(rewards={"reward": 1})
        )
        result = asyncio.run(
            verify_with_harbor(
                task,
                paths,
                environment,
                "harbor:result",
                CleanupReceipt(complete=complete),
            )
        )
        assert result.outcome == (
            RunOutcome.PASS if complete else RunOutcome.UNVERIFIED
        )
        assert factory.call_count == int(complete)
        assert factory.return_value.verify.await_count == int(complete)
