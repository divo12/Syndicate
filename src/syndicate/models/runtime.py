"""Pinned runtime identity, checked before any executor dispatch."""

import sys
from importlib.metadata import distribution, version
from typing import Literal, Self

from nexau.archs.main_sub.execution.stop_reason import AgentStopReason
from pydantic import BaseModel, ConfigDict, Field, model_validator

from syndicate.models.baseline import BaselineManifest
from syndicate.models.budget import BudgetCap, ProductRole
from syndicate.models.model_config import ModelSettings


class RuntimeIdentity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    harbor_version: Literal["0.22.0"] = "0.22.0"
    e2b_version: Literal["2.26.0"] = "2.26.0"
    nexau_version: Literal["0.3.9"] = "0.3.9"
    nexau_commit: Literal["35ee1861546db3cb280a6e17e38a74060d7c96c3"]


class _VcsInfo(BaseModel):
    vcs: Literal["git"]
    commit_id: str


class _SourceReceipt(BaseModel):
    vcs_info: _VcsInfo


def installed_runtime() -> RuntimeIdentity:
    """Inspect installed metadata, not an advertised config; never call a provider."""
    if sys.version_info[:2] != (3, 13):
        raise RuntimeError("Syndicate requires Python 3.13")
    source = distribution("nexau").read_text("direct_url.json")
    if source is None:
        raise RuntimeError("NexAU Git installation provenance is required")
    receipt = _SourceReceipt.model_validate_json(source)
    return RuntimeIdentity.model_validate(
        {
            "harbor_version": version("harbor"),
            "e2b_version": version("e2b"),
            "nexau_version": version("nexau"),
            "nexau_commit": receipt.vcs_info.commit_id,
        }
    )


class RuntimeRequest(BaseModel):
    """Controller-approved bounded dispatch; credentials travel separately."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)
    baseline: BaselineManifest
    harness_root: str = ""
    instruction: str = Field(min_length=1)
    budget: BudgetCap
    max_iterations: int = Field(gt=0)
    max_context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    shell_timeout_ms: int = Field(gt=0)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        reserved = self.max_iterations * (
            self.max_context_tokens + self.max_output_tokens
        )
        if reserved > self.budget.max_tokens:
            raise ValueError("Worst-case invocation tokens exceed dispatch budget")
        if self.max_output_tokens >= self.max_context_tokens:
            raise ValueError("Output reserve must leave room for input")
        if self.shell_timeout_ms > self.budget.max_seconds * 1000:
            raise ValueError("Shell timeout exceeds dispatch deadline")
        return self


class RoleDispatchRequest(BaseModel):
    """Bounded dispatch for a fixed product role; the caller owns credentials."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    model: ModelSettings
    role: ProductRole
    prompt: str = Field(min_length=1)
    budget: BudgetCap
    usage_ref: str = Field(min_length=1)
    max_iterations: int = Field(gt=0)
    max_context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_retries: Literal[0] = 0

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if not self.usage_ref.strip():
            raise ValueError("Usage reference must not be blank")
        reserved = self.max_iterations * (
            self.max_context_tokens + self.max_output_tokens
        )
        if reserved > self.budget.max_tokens:
            raise ValueError("Worst-case invocation tokens exceed dispatch budget")
        if self.max_output_tokens >= self.max_context_tokens:
            raise ValueError("Output reserve must leave room for input")
        return self


class RoleDispatchReceipt(BaseModel):
    """Returned in memory to the controller; never written as a payload artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    final_text: str
    usage_ref: str = Field(min_length=1)
    stop_reason: AgentStopReason | None
