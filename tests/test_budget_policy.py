import pytest
from pydantic import ValidationError

from syndicate.budget_policy import (
    BudgetCap,
    CampaignBudgetPolicy,
    ProductRole,
    RoleBudget,
)


def cap(tokens: int = 100, seconds: int = 60, spend: int = 1_000) -> BudgetCap:
    return BudgetCap(
        max_tokens=tokens,
        max_seconds=seconds,
        max_spend_microusd=spend,
    )


def policy() -> CampaignBudgetPolicy:
    role_budgets = tuple(RoleBudget(role=role, cap=cap()) for role in ProductRole)
    return CampaignBudgetPolicy(
        role_budgets=role_budgets,
        campaign_cap=cap(tokens=400, seconds=240, spend=4_000),
    )


def test_policy_has_one_bounded_cap_for_every_product_role() -> None:
    budget_policy = policy()

    assert budget_policy.budget_for(ProductRole.EXECUTOR) == cap()
    assert tuple(item.role for item in budget_policy.role_budgets) == tuple(ProductRole)


@pytest.mark.parametrize("field", ["max_tokens", "max_seconds", "max_spend_microusd"])
def test_caps_reject_missing_unbounded_or_invalid_values(field: str) -> None:
    valid = {"max_tokens": 100, "max_seconds": 60, "max_spend_microusd": 1_000}
    for value in (None, 0, -1, float("inf")):
        values = valid | {field: value}
        with pytest.raises(ValidationError):
            BudgetCap.model_validate(values)


def test_policy_rejects_missing_role_and_inconsistent_totals() -> None:
    role_budgets = tuple(
        RoleBudget(role=role, cap=cap())
        for role in ProductRole
        if role is not ProductRole.TASK_JUDGE
    )
    with pytest.raises(ValidationError):
        CampaignBudgetPolicy(role_budgets=role_budgets, campaign_cap=cap())
    with pytest.raises(ValidationError):
        CampaignBudgetPolicy(
            role_budgets=tuple(
                RoleBudget(role=role, cap=cap()) for role in ProductRole
            ),
            campaign_cap=cap(tokens=399, seconds=240, spend=4_000),
        )
