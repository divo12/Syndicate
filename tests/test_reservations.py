from pathlib import Path
from uuid import UUID

import pytest

from syndicate.models.budget import (
    BudgetCap,
    CampaignBudgetPolicy,
    ProductRole,
    RoleBudget,
)
from syndicate.models.reservations import ReservationState, Usage
from syndicate.services.reservations import ReservationLedger


def cap(spend: int = 10_000_000) -> BudgetCap:
    return BudgetCap(max_tokens=1_000, max_seconds=600, max_spend_microusd=spend)


def policy() -> CampaignBudgetPolicy:
    return CampaignBudgetPolicy(
        role_budgets=tuple(RoleBudget(role=role, cap=cap()) for role in ProductRole),
        campaign_cap=cap(),
    )


def test_reservations_are_persisted_and_share_the_campaign_ceiling(
    tmp_path: Path,
) -> None:
    ledger = ReservationLedger(tmp_path / "controller.sqlite", policy())

    reservation = ledger.reserve(ProductRole.EXECUTOR, UUID(int=1), cap(6_000_000))

    assert reservation.state is ReservationState.RESERVED
    assert (
        ReservationLedger(tmp_path / "controller.sqlite", policy()).get(
            reservation.operation_id
        )
        == reservation
    )
    with pytest.raises(ValueError, match="campaign"):
        ledger.reserve(ProductRole.TASK_JUDGE, UUID(int=2), cap(4_000_001))


def test_unknown_usage_and_cancelled_work_keep_their_full_reservation(
    tmp_path: Path,
) -> None:
    ledger = ReservationLedger(tmp_path / "controller.sqlite", policy())
    first = ledger.reserve(ProductRole.EXECUTOR, UUID(int=1), cap(6_000_000))
    cancelled = ledger.cancel(first.operation_id)

    assert cancelled.state is ReservationState.CANCELLED
    with pytest.raises(ValueError, match="campaign"):
        ledger.reserve(ProductRole.TASK_JUDGE, UUID(int=2), cap(4_000_001))


def test_reconciled_usage_releases_only_measured_headroom_and_retries_are_new_attempts(
    tmp_path: Path,
) -> None:
    ledger = ReservationLedger(tmp_path / "controller.sqlite", policy())
    first = ledger.reserve(ProductRole.EXECUTOR, UUID(int=1), cap(6_000_000))
    reconciled = ledger.reconcile(
        first.operation_id,
        Usage(tokens=400, seconds=20, spend_microusd=2_000_000),
    )
    retry = ledger.reserve_retry(reconciled.operation_id, UUID(int=2))

    assert reconciled.state is ReservationState.RECONCILED
    assert retry.retry_of == first.operation_id
    assert retry.operation_id != first.operation_id
    assert ledger.reserve(ProductRole.TASK_JUDGE, UUID(int=3), cap(2_000_000))
