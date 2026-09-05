"""Controller-private, offline input validation; never dispatch a model."""

import hashlib
from pathlib import Path
from typing import Literal
from uuid import UUID

from syndicate import benchmark_manifest as benchmark
from syndicate.budget_policy import CampaignBudgetPolicy
from syndicate.cli_envelope import Digest, PreflightCommand, WireModel
from syndicate.model_config import ModelSettings, load_model_config


class AdmissionError(ValueError):
    """Request does not match the controller declaration."""


class ControllerConfig(WireModel):
    """Trusted declaration, stored separately from untrusted command requests."""

    env_file: Path
    benchmark_root: Path
    assignments: tuple[benchmark.Assignment, ...]
    approved_manifest_hash: Digest
    approved_config_hash: Digest
    approved_request_hashes: tuple[Digest, ...]
    budget: CampaignBudgetPolicy


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


def preflight(
    command: PreflightCommand, controller: ControllerConfig
) -> PreflightResult:
    manifest = benchmark.BenchmarkManifest.load(
        controller.benchmark_root, benchmark.ITSMBENCH_REVISION, controller.assignments
    )
    if manifest.content_hash != controller.approved_manifest_hash:
        raise ValueError("Controller registry does not match approved hash")
    if command.manifest_hash != controller.approved_manifest_hash:
        raise AdmissionError("Request registry is not controller approved")
    if command.content_hash not in controller.approved_request_hashes:
        raise AdmissionError("Request is not controller approved")
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
