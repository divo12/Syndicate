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
        if tuple(item.role for item in self.role_budgets) != tuple(ProductRole):
            raise ValueError("Role budgets must cover each product role exactly once")
        role_total = BudgetCap(
            max_tokens=sum(item.cap.max_tokens for item in self.role_budgets),
            max_seconds=sum(item.cap.max_seconds for item in self.role_budgets),
            max_spend_microusd=sum(
                item.cap.max_spend_microusd for item in self.role_budgets
            ),
        )
        if role_total != self.campaign_cap:
            raise ValueError("Campaign cap must equal the total of role caps")
        return self

    def budget_for(self, role: ProductRole) -> BudgetCap:
        for item in self.role_budgets:
            if item.role is role:
                return item.cap
        raise ValueError("Role is not present in this policy")
