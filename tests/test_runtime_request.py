from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from syndicate.models.baseline import PromptVariables, prepare_baseline
from syndicate.models.budget import BudgetCap
from syndicate.models.model_config import ModelSettings
from syndicate.models.runtime import RuntimeRequest


def request() -> RuntimeRequest:
    root = Path(__file__).resolve().parents[1]
    baseline = prepare_baseline(
        root / "harnesses/seed",
        root / "requirements.lock",
        ModelSettings(
            endpoint="https://azure.example/openai/v1/", deployment="gpt-5.4-mini"
        ),
        PromptVariables(
            date=date(2026, 9, 6), username="syndicate", working_directory="/app"
        ),
    )
    return RuntimeRequest(
        baseline=baseline,
        instruction="Use the shell to check the workspace.",
        budget=BudgetCap(max_tokens=50000, max_seconds=30, max_spend_microusd=100000),
        max_iterations=3,
        max_context_tokens=12000,
        max_output_tokens=1000,
        shell_timeout_ms=1000,
    )


def test_bounded_request_round_trips() -> None:
    value = request()
    assert RuntimeRequest.model_validate_json(value.model_dump_json()) == value
    assert value.baseline.model.deployment == "gpt-5.4-mini"


@pytest.mark.parametrize(
    "changes",
    [
        '{"max_iterations":10}',
        '{"max_output_tokens":12000}',
        '{"shell_timeout_ms":31000}',
        '{"instruction":""}',
    ],
)
def test_invalid_dispatch_bounds(changes: str) -> None:
    import json

    wire = json.loads(request().model_dump_json())
    wire.update(json.loads(changes))
    with pytest.raises(ValidationError):
        RuntimeRequest.model_validate_json(json.dumps(wire))


def test_runtime_requires_explicit_credential() -> None:
    import asyncio
    from unittest.mock import AsyncMock, patch

    from pydantic import SecretStr

    from syndicate.services.runtime import run_on_controller

    root = Path(__file__).resolve().parents[1]
    with patch("syndicate.services.runtime.E2BShell") as backend:
        with pytest.raises(ValueError, match="Explicit API credential"):
            with asyncio.Runner() as runner:
                runner.run(
                    run_on_controller(
                        request(),
                        SecretStr(" "),
                        AsyncMock(),
                        harness_dir=root / "harnesses/seed",
                        framework_lock=root / "requirements.lock",
                    )
                )
    backend.assert_not_called()
