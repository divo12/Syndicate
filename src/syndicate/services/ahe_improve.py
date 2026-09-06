"""AHE evaluate→analyze→improve step used between Harbor generations."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from nexau import Tool
from openai import OpenAI

from syndicate.models.budget import BudgetCap, ProductRole
from syndicate.models.model_config import (
    ModelConfig,
    ModelConfigError,
    load_model_config,
)
from syndicate.models.runtime import RoleDispatchRequest
from syndicate.services.agent_debugger import DebugRunner, debug_generation
from syndicate.services.corpus_tools import read_tools, write_tools
from syndicate.services.debug_corpus import materialize_analysis
from syndicate.services.evolve_harness import (
    EvolveReceipt,
    EvolveRunner,
    evolve_workspace,
)
from syndicate.services.failure_mine import mine_latest
from syndicate.services.runtime import dispatch_role

Analyze = Callable[[Path], tuple[Path, ...]]
Evolve = Callable[[Path, Path, Path], EvolveReceipt]


def improve_generation(
    artifact_root: Path,
    next_generation: int,
    seed: Path,
    *,
    analyze: Analyze | None = None,
    evolve: Evolve | None = None,
) -> Path | None:
    """Mine the latest Harbor trials, debug each task, then evolve the harness."""
    runs = artifact_root / "runs"
    mine = mine_latest(runs, next_generation - 1)
    if not mine.tasks:
        return None
    analysis = artifact_root / "analysis" / f"gen-{next_generation - 1}"
    materialize_analysis(runs, analysis)
    (analyze or live_analyze)(analysis)
    dest = artifact_root / "harnesses" / f"gen-{next_generation}"
    (evolve or live_evolve)(analysis, seed, dest)
    return dest


def live_analyze(analysis: Path) -> tuple[Path, ...]:
    config = _model_config()

    def runner(task_id: str, query: str, corpus: Path) -> str:
        del task_id
        return _dispatch(
            config,
            ProductRole.TASK_JUDGE,
            query,
            read_tools(corpus),
        )

    return debug_generation(analysis, runner)


def live_evolve(analysis: Path, seed: Path, dest: Path) -> EvolveReceipt:
    config = _model_config()

    def runner(prompt: str, workspace: Path) -> str:
        return _dispatch(
            config,
            ProductRole.IMPROVEMENT_AGENT,
            prompt,
            write_tools(workspace),
        )

    return evolve_workspace(analysis, seed, dest, runner)


def live_debug_runner() -> DebugRunner:
    config = _model_config()

    def runner(task_id: str, query: str, corpus: Path) -> str:
        del task_id
        return _dispatch(config, ProductRole.TASK_JUDGE, query, read_tools(corpus))

    return runner


def live_evolve_runner() -> EvolveRunner:
    config = _model_config()

    def runner(prompt: str, workspace: Path) -> str:
        return _dispatch(
            config, ProductRole.IMPROVEMENT_AGENT, prompt, write_tools(workspace)
        )

    return runner


def _model_config() -> ModelConfig:
    try:
        return load_model_config(Path(os.environ.get("SYNDICATE_ENV_FILE", ".env")))
    except ModelConfigError as error:
        raise ValueError("AHE analyze/improve requires Azure model config") from error


def _dispatch(
    config: ModelConfig,
    role: ProductRole,
    prompt: str,
    tools: tuple[Tool, ...],
) -> str:
    request = RoleDispatchRequest(
        model=config.settings,
        role=role,
        prompt=prompt,
        budget=BudgetCap(
            max_tokens=300_000, max_seconds=240, max_spend_microusd=300_000
        ),
        usage_ref=str(uuid4()),
        max_iterations=12,
        max_context_tokens=20_000,
        max_output_tokens=2_000,
    )
    with OpenAI(
        api_key=config.api_key.get_secret_value(),
        base_url=config.settings.endpoint,
        max_retries=0,
        timeout=180,
    ) as client:
        import asyncio

        receipt = asyncio.run(
            dispatch_role(request, tools, client, accept_incomplete=True)
        )
    return receipt.final_text
