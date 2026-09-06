"""Immutable public-only inputs and generated task rubric contracts."""

import hashlib
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from syndicate.models.budget import BudgetCap
from syndicate.models.evidence import Citation

Text = Annotated[str, Field(min_length=1, pattern=r"\S")]


class JudgeObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class RequirementKind(StrEnum):
    GOAL = "goal"
    POLICY = "policy"
    TOOL_METADATA = "tool_metadata"
    TRUSTED_CRITERION = "trusted_criterion"


class PublicRequirement(JudgeObject):
    """Controller-projected public metadata, never hidden checks or answers."""

    reference: Text
    kind: RequirementKind
    text: Text


class JudgeBuildRequest(JudgeObject):
    campaign_id: Text
    task_id: Text
    requirements: tuple[PublicRequirement, ...] = Field(min_length=1)
    budget: BudgetCap

    @model_validator(mode="after")
    def public_goal(self) -> Self:
        refs = [item.reference for item in self.requirements]
        if len(set(refs)) != len(refs):
            raise ValueError("Duplicate public requirement reference")
        if not any(item.kind is RequirementKind.GOAL for item in self.requirements):
            raise ValueError("Public goal required")
        return self

    @property
    def input_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class CriterionStatus(StrEnum):
    SUPPORTED = "supported"
    UNRESOLVED = "unresolved"


class EvidenceKind(StrEnum):
    TRAJECTORY = "trajectory"
    RUN_RECORD = "run_record"
    AUDIT = "audit"
    VERIFIER = "verifier_result"


class JudgeTool(StrEnum):
    MANIFEST = "get_trace_manifest"
    SEARCH = "search_trajectory"
    SPAN = "read_span_context"
    RECORD = "read_run_record"
    AUDIT = "read_audit_history"
    VERIFIER = "read_verifier_result"


class SupportQuote(JudgeObject):
    reference: Text
    quote: Text


class Criterion(JudgeObject):
    criterion_id: Text
    description: Text
    status: CriterionStatus
    support: tuple[SupportQuote, ...] = ()
    evidence_requirements: tuple[EvidenceKind, ...] = Field(min_length=1)
    unresolved_reason: Text | None = None

    @model_validator(mode="after")
    def supported_or_unresolved(self) -> Self:
        if self.status is CriterionStatus.SUPPORTED:
            if not self.support or self.unresolved_reason is not None:
                raise ValueError("Supported criteria require public support")
        elif self.unresolved_reason is None:
            raise ValueError("Unresolved criteria require missing context")
        return self


class JudgeDraft(JudgeObject):
    criteria: tuple[Criterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_criteria(self) -> Self:
        if len({item.criterion_id for item in self.criteria}) != len(self.criteria):
            raise ValueError("Duplicate criterion ID")
        return self


class JudgeSpec(JudgeDraft):
    """Schema/support validated; behavioral admission is a separate execution gate."""

    schema_version: Literal[1] = 1
    campaign_id: Text
    task_id: Text
    input_hash: Text
    model: Literal["gpt-5.4-mini"] = "gpt-5.4-mini"
    allowed_tools: tuple[JudgeTool, ...] = tuple(JudgeTool)
    budget: BudgetCap
    prompt: Text

    @property
    def spec_hash(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class ReportStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class FindingCategory(StrEnum):
    UNSUPPORTED_CLAIM = "unsupported_claim"
    WRONG_ENTITY = "wrong_entity"
    WRONG_OPERATION = "wrong_operation"
    WRONG_ARGUMENTS = "wrong_arguments"
    MISSED_RECORDS = "missed_records"
    POLICY_CONTRADICTION = "policy_contradiction"
    INEFFECTIVE_CHANGE = "ineffective_change"
    RECOVERED_ERROR = "recovered_error"
    REDUNDANT_WORK = "redundant_work"
    SUCCESSFUL_STRATEGY = "successful_strategy"


class Finding(JudgeObject):
    finding_id: Text
    category: FindingCategory
    observation: Text
    hypothesis: Text | None = None
    evidence: tuple[Citation, ...] = Field(min_length=1)


class RunCoverage(JudgeObject):
    run_id: UUID
    examined: tuple[Citation, ...] = ()
    unread_relevant: tuple[Citation, ...] = ()

    @model_validator(mode="after")
    def same_run(self) -> Self:
        if any(
            ref.run_id != self.run_id for ref in self.examined + self.unread_relevant
        ):
            raise ValueError("Coverage reference belongs to another run")
        return self


class ReportDraft(JudgeObject):
    """Model findings supplement the trusted verifier; no reward field is accepted."""

    run_ids: tuple[UUID, ...] = Field(min_length=1)
    status: ReportStatus
    findings: tuple[Finding, ...] = ()
    unresolved_questions: tuple[Text, ...] = ()
    coverage: tuple[RunCoverage, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_identifiers(self) -> Self:
        groups = (
            self.run_ids,
            tuple(item.run_id for item in self.coverage),
            tuple(item.finding_id for item in self.findings),
        )
        if any(len(set(group)) != len(group) for group in groups):
            raise ValueError("Duplicate report identifier")
        return self


class TaskReport(ReportDraft):
    task_id: Text
    judge_spec_hash: Text
