"""Typed, offline campaign budget policy validation."""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProductRole(StrEnum):
    """All roles use the P02 fixed GPT-5.4-mini model configuration."""

    EXECUTOR = "executor"
    JUDGE_BUILDER = "judge_builder"
    TASK_JUDGE = "task_judge"
    IMPROVEMENT_AGENT = "improvement_agent"


class BudgetCap(BaseModel):
    """Finite cumulative allowance; spend is integer micro-USD."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_tokens: int = Field(gt=0)
    max_seconds: int = Field(gt=0)
    max_spend_microusd: int = Field(gt=0)

    def fits_within(self, ceiling: Self) -> bool:
        return (
            self.max_tokens <= ceiling.max_tokens
            and self.max_seconds <= ceiling.max_seconds
            and self.max_spend_microusd <= ceiling.max_spend_microusd
        )


class RoleBudget(BaseModel):
    """A bounded allowance assigned to exactly one product role."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    role: ProductRole
    cap: BudgetCap


class CampaignBudgetPolicy(BaseModel):
    """Complete preflight policy, without reservations or model dispatch."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    role_budgets: tuple[RoleBudget, ...] = Field(min_length=len(ProductRole))
    campaign_cap: BudgetCap

    @model_validator(mode="after")
    def validate_totals(self) -> Self:
        if len(self.role_budgets) != len(ProductRole) or {
            item.role for item in self.role_budgets
        } != set(ProductRole):
            raise ValueError("Role budgets must cover each product role exactly once")
        for item in self.role_budgets:
            if not item.cap.fits_within(self.campaign_cap):
                raise ValueError("Role cap must fit within the campaign cap")
        return self

    def budget_for(self, role: ProductRole) -> BudgetCap:
        for item in self.role_budgets:
            if item.role is role:
                return item.cap
        raise ValueError("Role is not present in this policy")
