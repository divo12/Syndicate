"""Read-only campaign projection assembled from validated receipts."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from syndicate.models.improvement import HarnessChangeManifest
from syndicate.models.judging import TaskReport


class ReceiptSource(StrEnum):
    RECORDED = "recorded"
    SYNTHETIC = "synthetic"


class ReviewCampaign(BaseModel):
    """A display projection; it deliberately owns no experiment execution state."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    campaign_id: str = Field(min_length=1, pattern=r"\S")
    source: ReceiptSource
    reports: tuple[TaskReport, ...] = Field(min_length=1)
    candidates: tuple[HarnessChangeManifest, ...] = ()

    @model_validator(mode="after")
    def matching_campaign_and_unique_tasks(self) -> "ReviewCampaign":
        if len({report.task_id for report in self.reports}) != len(self.reports):
            raise ValueError("Review campaign reports must have unique task IDs")
        if any(
            candidate.diagnosis.campaign_id != self.campaign_id
            for candidate in self.candidates
        ):
            raise ValueError("Candidate diagnosis belongs to another campaign")
        return self
