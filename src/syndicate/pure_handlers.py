"""Thin controller adapters around pure collection, comparison, and lineage logic."""

from pydantic import BaseModel, ConfigDict, Field

from syndicate.artifact_store import ArtifactStore
from syndicate.cli_envelope import (
    ArtifactKind,
    ArtifactRef,
    CollectReportsCommand,
    CommandReceipt,
    CommandStatus,
    CompareHarnessCommand,
    SelectHarnessCommand,
)
from syndicate.comparison_contracts import PairSchedule
from syndicate.judge_contracts import TaskReport
from syndicate.lineage import (
    HarnessLineage,
    HarnessVersion,
    PromotionReceipt,
    PromotionStatus,
)
from syndicate.report_collection import (
    ExpectedTaskReport,
    collect_reports,
)
from syndicate.selection import assess_comparison
from syndicate.selection_contracts import (
    ComparisonAssessment,
    ComparisonDecision,
    ComparisonPolicy,
    TrialMeasurement,
)


class HandlerModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ExpectedReportsArtifact(HandlerModel):
    reports: tuple[ExpectedTaskReport, ...] = Field(min_length=1)


class MeasurementsArtifact(HandlerModel):
    measurements: tuple[TrialMeasurement, ...] = Field(min_length=1)


def collect(command: CollectReportsCommand, store: ArtifactStore) -> CommandReceipt:
    expected = store.load(command.expected_reports_ref, ExpectedReportsArtifact)
    reports = tuple(
        store.load(reference, TaskReport) for reference in command.report_refs
    )
    output = collect_reports(expected.reports, reports)
    return _receipt(command, store.write(command, ArtifactKind.COLLECTION, output))


def compare(command: CompareHarnessCommand, store: ArtifactStore) -> CommandReceipt:
    schedule = store.load(command.schedule_ref, PairSchedule)
    policy = store.load(command.policy_ref, ComparisonPolicy)
    measurements = store.load(command.measurements_ref, MeasurementsArtifact)
    _aligned(schedule, measurements.measurements)
    output = assess_comparison(policy, measurements.measurements)
    return _receipt(command, store.write(command, ArtifactKind.ASSESSMENT, output))


def select(command: SelectHarnessCommand, store: ArtifactStore) -> CommandReceipt:
    assessment = store.load(command.assessment_ref, ComparisonAssessment)
    version = store.load(command.lineage_ref, HarnessVersion)
    lineage = HarnessLineage(
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
        if assessment.decision is ComparisonDecision.PROMOTE
        else PromotionReceipt(
            operation_id=command.operation_id,
            status=PromotionStatus.STALE,
            previous=version,
            current=version,
        )
    )
    return _receipt(command, store.write(command, ArtifactKind.PROMOTION, output))


def _aligned(
    schedule: PairSchedule, measurements: tuple[TrialMeasurement, ...]
) -> None:
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
