"""Typed Markdown and JSON delivery views; never calculate campaign outcomes."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from syndicate.models.review import ReceiptSource, ReviewCampaign
from syndicate.models.selection import ComparisonAssessment


class HeldOutStatus(StrEnum):
    NOT_RUN = "not_run"
    BLOCKED = "blocked"
    COMPLETE = "complete"


class HeldOutEvaluation(BaseModel):
    """A status projection, not a replacement source for held-out evidence."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    status: HeldOutStatus
    task_ids: tuple[str, ...] = Field(min_length=1)
    limitation: str = Field(min_length=1)


class ReviewReport(BaseModel):
    """Portable report assembled from campaign and M4 selection receipts."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    campaign: ReviewCampaign
    assessment: ComparisonAssessment
    held_out: HeldOutEvaluation
    limitations: tuple[str, ...] = Field(min_length=1)

    def to_json(self) -> str:
        return self.model_dump_json()

    def to_markdown(self) -> str:
        source = (
            "Synthetic preparation data"
            if self.campaign.source is ReceiptSource.SYNTHETIC
            else "Recorded receipts"
        )
        limitations = "\n".join(f"- {item}" for item in self.limitations)
        return (
            f"# Campaign {self.campaign.campaign_id}\n\n"
            f"Source: {source}\n\n"
            f"Comparison decision: {self.assessment.decision.value}\n\n"
            f"Held-out status: {self.held_out.status.value}\n\n"
            f"Held-out limitation: {self.held_out.limitation}\n\n"
            f"## Limitations\n\n{limitations}\n"
        )
