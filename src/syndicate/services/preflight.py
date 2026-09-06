"""Controller-private, offline input validation; never dispatch a model."""

import hashlib
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from syndicate.models.budget import CampaignBudgetPolicy
from syndicate.models.envelope import Digest, PreflightCommand, WireModel
from syndicate.models.model_config import ModelSettings, load_model_config
from syndicate.repositories import benchmark_manifest as benchmark


class AdmissionError(ValueError):
    """Request does not match the controller declaration."""


class InfrastructureError(ValueError):
    """Controller or benchmark infrastructure is unavailable or invalid."""


class PreflightConfig(WireModel):
    """Operator-owned campaign inputs; relative paths are based on the config file."""

    env_file: Path
    benchmark_root: Path
    assignments: tuple[benchmark.Assignment, ...]
    budget: CampaignBudgetPolicy


class ControllerConfig(PreflightConfig):
    """Trusted declaration, stored separately from untrusted command requests."""

    approved_manifest_hash: Digest
    approved_config_hash: Digest
    approved_request_hashes: tuple[Digest, ...]


class PreflightResult(WireModel):
    operation_id: UUID
    attempt_id: UUID
    configuration_valid: Literal[True] = True
    live_model_verified: Literal[False] = False
    manifest_hash: Digest
    model_settings_hash: Digest


def configuration_hash(settings: ModelSettings, budget: CampaignBudgetPolicy) -> str:
    """Seal nonsecret model settings and the validated, ordered budget policy."""
    payload = settings.canonical_json() + "\n" + budget.model_dump_json()
    return hashlib.sha256(payload.encode()).hexdigest()


def _load_manifest(config: PreflightConfig) -> benchmark.BenchmarkManifest:
    try:
        return benchmark.BenchmarkManifest.load(
            config.benchmark_root, benchmark.ITSMBENCH_REVISION, config.assignments
        )
    except ValueError:
        raise InfrastructureError("Benchmark checkout validation failed") from None


def prepare_preflight(config_file: Path) -> tuple[PreflightCommand, ControllerConfig]:
    """Admit a fresh offline request from trusted operator configuration."""
    config_file = config_file.resolve(strict=True)
    config = PreflightConfig.model_validate_json(config_file.read_bytes())
    env_file = (config_file.parent / config.env_file).resolve(strict=True)
    benchmark_root = (config_file.parent / config.benchmark_root).resolve(strict=True)
    manifest = _load_manifest(
        config.model_copy(update={"benchmark_root": benchmark_root})
    )
    settings = load_model_config(env_file).settings
    command = PreflightCommand(
        operation_id=uuid4(), attempt_id=uuid4(), manifest_hash=manifest.content_hash
    )
    controller = ControllerConfig(
        env_file=env_file,
        benchmark_root=benchmark_root,
        assignments=config.assignments,
        budget=config.budget,
        approved_manifest_hash=manifest.content_hash,
        approved_config_hash=configuration_hash(settings, config.budget),
        approved_request_hashes=(command.content_hash,),
    )
    return command, controller


def preflight(
    command: PreflightCommand, controller: ControllerConfig
) -> PreflightResult:
    if command.manifest_hash != controller.approved_manifest_hash:
        raise AdmissionError("Request registry is not controller approved")
    if command.content_hash not in controller.approved_request_hashes:
        raise AdmissionError("Request is not controller approved")
    manifest = _load_manifest(controller)
    if manifest.content_hash != controller.approved_manifest_hash:
        raise ValueError("Controller registry does not match approved hash")
    model = load_model_config(controller.env_file)
    if (
        configuration_hash(model.settings, controller.budget)
        != controller.approved_config_hash
    ):
        raise ValueError("Configuration is not controller approved")
    return PreflightResult(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        manifest_hash=manifest.content_hash,
        model_settings_hash=model.settings.settings_hash,
    )
