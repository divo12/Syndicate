"""Bind JobWorker harbor execution to the live controller trial handler."""

import os
from datetime import date
from pathlib import Path
from uuid import uuid4

from syndicate.controllers.handler_inputs import RuntimeInput
from syndicate.controllers.live_handlers import LiveHandlers, run
from syndicate.models.baseline import PromptVariables, bind_harness
from syndicate.models.budget import BudgetCap
from syndicate.models.commands import RunTrialCommand
from syndicate.models.envelope import ArtifactKind, ArtifactRef
from syndicate.models.jobs import TaskResult
from syndicate.models.model_config import (
    ModelConfig,
    ModelConfigError,
    load_model_config,
)
from syndicate.models.runtime import RuntimeRequest
from syndicate.repositories.artifact_store import ArtifactStore
from syndicate.repositories.benchmark_manifest import Assignment, Split
from syndicate.services.benchmark import RunReceipt
from syndicate.services.executors import task_result_from_verifier
from syndicate.services.failure_mine import lessons_for

_DIGEST = "0" * 64
_REPO = Path(__file__).resolve().parents[3]


def run_bound_harbor_task(task_id: str, generation: int) -> TaskResult:
    config = _model_config()
    store = ArtifactStore(_artifact_root())
    command = _command(task_id, store, config, generation)
    receipt = run(
        command,
        store,
        config.api_key,
        LiveHandlers(),
        Path(os.environ.get("BENCHMARK_ROOT", "benchmark/ITSMBench")),
        (Assignment(task_id, Split.DEVELOPMENT, "job"),),
    )
    result = store.load(receipt.artifact_refs[0], RunReceipt)
    return task_result_from_verifier(task_id, result.verifier)


def _artifact_root() -> Path:
    return Path(os.environ.get("ARTIFACT_ROOT", ".syndicate")).resolve()


def _first_existing(paths: tuple[Path, ...], kind: str) -> Path:
    for path in paths:
        if kind == "dir" and path.is_dir():
            return path
        if kind == "file" and path.is_file():
            return path
    raise ValueError(f"Harbor {kind} is not mounted")


def _harness_for(generation: int) -> Path:
    evolved = _artifact_root() / "harnesses" / f"gen-{generation}"
    if (evolved / "systemprompt.md").is_file():
        return evolved
    return _harness_seed()


def _harness_seed() -> Path:
    configured = os.environ.get("HARNESS_SEED")
    return _first_existing(
        (
            *(() if configured is None else (Path(configured),)),
            Path("/app/harnesses/seed"),
            _REPO / "harnesses" / "seed",
            Path.cwd() / "harnesses" / "seed",
        ),
        "dir",
    )


def _framework_lock() -> Path:
    configured = os.environ.get("FRAMEWORK_LOCK")
    return _first_existing(
        (
            *(() if configured is None else (Path(configured),)),
            Path("/app/requirements.lock"),
            _REPO / "requirements.lock",
            Path.cwd() / "requirements.lock",
        ),
        "file",
    )


def _model_config() -> ModelConfig:
    try:
        return load_model_config(Path(os.environ.get("SYNDICATE_ENV_FILE", ".env")))
    except ModelConfigError as error:
        raise ValueError("Harbor live dispatch requires Azure model config") from error


def _command(
    task_id: str, store: ArtifactStore, config: ModelConfig, generation: int
) -> RunTrialCommand:
    operation_id = uuid4()
    attempt_id = uuid4()
    budget = BudgetCap(max_tokens=50_000, max_seconds=180, max_spend_microusd=100_000)
    request = _runtime_request(task_id, config, budget, generation)
    placeholder = ArtifactRef(
        kind=ArtifactKind.RUNTIME_REQUEST,
        operation_id=operation_id,
        attempt_id=attempt_id,
        sha256=_DIGEST,
    )
    draft = RunTrialCommand(
        operation_id=operation_id,
        attempt_id=attempt_id,
        task_id=task_id,
        harness_hash=request.baseline.identity_hash,
        memory_hash=_DIGEST,
        model_config_hash=config.settings.settings_hash,
        runtime_image_hash=_DIGEST,
        judge_spec_hash=_DIGEST,
        verifier_version="harbor",
        runtime_request_ref=placeholder,
        budget=budget,
    )
    reference = store.write(
        draft, ArtifactKind.RUNTIME_REQUEST, RuntimeInput(request=request)
    )
    return draft.model_copy(update={"runtime_request_ref": reference})


def _task_instruction(task_id: str) -> str:
    from harbor.models.task.task import strip_canary

    root = Path(os.environ.get("BENCHMARK_ROOT", "benchmark/ITSMBench"))
    path = root / "tasks" / task_id / "instruction.md"
    if not path.is_file():
        raise ValueError("Task instruction is not mounted")
    return strip_canary(path.read_text(encoding="utf-8"))


def _runtime_request(
    task_id: str, config: ModelConfig, budget: BudgetCap, generation: int
) -> RuntimeRequest:
    return RuntimeRequest(
        baseline=bind_harness(
            _harness_for(generation),
            _framework_lock(),
            config.settings,
            PromptVariables(
                date=date(2026, 9, 6),
                username="syndicate",
                working_directory="/app",
            ),
            lessons_for(_artifact_root(), generation),
        ),
        harness_root=_harness_for(generation).as_posix(),
        instruction=_task_instruction(task_id),
        budget=budget,
        max_iterations=3,
        max_context_tokens=12_000,
        max_output_tokens=1_000,
        shell_timeout_ms=1_000,
    )
