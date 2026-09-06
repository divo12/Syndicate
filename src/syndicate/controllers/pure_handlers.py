"""Thin controller adapters around leftover collection, comparison, and lineage."""

from importlib import import_module
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from syndicate.models.commands import (
    CollectReportsCommand,
    CompareHarnessCommand,
    SelectHarnessCommand,
)
from syndicate.models.envelope import (
    ArtifactKind,
    ArtifactRef,
    CommandReceipt,
    CommandStatus,
)
from syndicate.repositories.artifact_store import ArtifactStore


class HandlerModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class _TrialLike(Protocol):
    trial_id: object
    arm: object


class _ScheduleLike(Protocol):
    trials: tuple[_TrialLike, ...]


def collect(command: CollectReportsCommand, store: ArtifactStore) -> CommandReceipt:
    expected_type = import_module("syndicate.models.collection").ExpectedTaskReport
    report_type = import_module("syndicate.models.judging").TaskReport
    collect_reports = import_module("syndicate.services.collection").collect_reports

    class ExpectedReportsArtifact(HandlerModel):
        reports: tuple[expected_type, ...] = Field(min_length=1)  # type: ignore[valid-type]

    expected = store.load(command.expected_reports_ref, ExpectedReportsArtifact)
    reports = tuple(
        store.load(reference, report_type) for reference in command.report_refs
    )
    output = collect_reports(expected.reports, reports)
    return _receipt(command, store.write(command, ArtifactKind.COLLECTION, output))


def compare(command: CompareHarnessCommand, store: ArtifactStore) -> CommandReceipt:
    schedule_type = import_module("syndicate.models.comparison").PairSchedule
    selection = import_module("syndicate.models.selection")
    assess_comparison = import_module("syndicate.services.selection").assess_comparison
    measurement_type = selection.TrialMeasurement

    class MeasurementsArtifact(HandlerModel):
        measurements: tuple[measurement_type, ...] = Field(min_length=1)  # type: ignore[valid-type]

    schedule = store.load(command.schedule_ref, schedule_type)
    policy = store.load(command.policy_ref, selection.ComparisonPolicy)
    measurements = store.load(command.measurements_ref, MeasurementsArtifact)
    _aligned(schedule, measurements.measurements)
    output = assess_comparison(policy, measurements.measurements)
    return _receipt(command, store.write(command, ArtifactKind.ASSESSMENT, output))


def select(command: SelectHarnessCommand, store: ArtifactStore) -> CommandReceipt:
    lineage_models = import_module("syndicate.models.lineage")
    selection = import_module("syndicate.models.selection")
    lineage_type = import_module("syndicate.services.lineage").HarnessLineage
    assessment = store.load(command.assessment_ref, selection.ComparisonAssessment)
    version = store.load(command.lineage_ref, lineage_models.HarnessVersion)
    lineage = lineage_type(
        store.root / "lineage.sqlite", version.harness_hash, version.memory_hash
    )
    if (
        lineage.current() != version
        or command.parent_harness_hash != version.harness_hash
    ):
        raise ValueError("Selection lineage does not match current incumbent")
    output = (
        lineage.promote(
            command.operation_id,
            command.parent_harness_hash,
            command.candidate_harness_hash,
            command.candidate_memory_hash,
        )
        if assessment.decision is selection.ComparisonDecision.PROMOTE
        else lineage_models.PromotionReceipt(
            operation_id=command.operation_id,
            status=lineage_models.PromotionStatus.STALE,
            previous=version,
            current=version,
        )
    )
    return _receipt(command, store.write(command, ArtifactKind.PROMOTION, output))


def _aligned(schedule: _ScheduleLike, measurements: tuple[_TrialLike, ...]) -> None:
    scheduled = {(trial.trial_id, trial.arm) for trial in schedule.trials}
    observed = {(item.trial_id, item.arm) for item in measurements}
    if scheduled != observed or len(observed) != len(measurements):
        raise ValueError("Measurements do not align with scheduled trial IDs and arms")


def _receipt(
    command: CollectReportsCommand | CompareHarnessCommand | SelectHarnessCommand,
    reference: ArtifactRef,
) -> CommandReceipt:
    return CommandReceipt(
        operation_id=command.operation_id,
        attempt_id=command.attempt_id,
        status=CommandStatus.COMPLETED,
        artifact_refs=(reference,),
    )
