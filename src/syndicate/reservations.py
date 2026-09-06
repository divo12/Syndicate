"""SQLite-backed, conservative accounting for controller-owned model work."""

import sqlite3
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from syndicate.budget_policy import BudgetCap, CampaignBudgetPolicy, ProductRole

_LIVE_CEILING_MICROUSD = 10_000_000
SqlValue = str | int | None
SqlRow = tuple[SqlValue, ...]


class ReservationObject(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ReservationState(StrEnum):
    RESERVED = "reserved"
    RECONCILED = "reconciled"
    CANCELLED = "cancelled"


class Usage(ReservationObject):
    tokens: int = Field(ge=0)
    seconds: int = Field(ge=0)
    spend_microusd: int = Field(ge=0)


class UsageReservation(ReservationObject):
    operation_id: UUID
    role: ProductRole
    cap: BudgetCap
    state: ReservationState
    retry_of: UUID | None = None
    usage: Usage | None = None


class ReservationLedger:
    """Keep all role attempts under one verified $10 campaign allowance."""

    def __init__(self, path: Path, policy: CampaignBudgetPolicy) -> None:
        if policy.campaign_cap.max_spend_microusd > _LIVE_CEILING_MICROUSD:
            raise ValueError("Campaign cap exceeds the $10 live validation ceiling")
        self._path = path
        self._policy = policy
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS usage_reservations (
                operation_id TEXT PRIMARY KEY, role TEXT NOT NULL,
                max_tokens INTEGER NOT NULL, max_seconds INTEGER NOT NULL,
                max_spend INTEGER NOT NULL, state TEXT NOT NULL,
                retry_of TEXT, used_tokens INTEGER, used_seconds INTEGER,
                used_spend INTEGER)"""
            )

    def reserve(
        self, role: ProductRole, operation_id: UUID, cap: BudgetCap
    ) -> UsageReservation:
        if not cap.fits_within(self._policy.budget_for(role)):
            raise ValueError("Reservation exceeds its role budget")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = self._get(connection, operation_id)
            if existing is not None:
                if existing.role is role and existing.cap == cap:
                    return existing
                raise ValueError("Operation already has a different reservation")
            if self._committed_spend(connection) + cap.max_spend_microusd > (
                self._policy.campaign_cap.max_spend_microusd
            ):
                raise ValueError("Reservation exceeds the shared campaign budget")
            reservation = UsageReservation(
                operation_id=operation_id,
                role=role,
                cap=cap,
                state=ReservationState.RESERVED,
            )
            self._insert(connection, reservation)
            return reservation

    def reserve_retry(
        self, prior_operation_id: UUID, operation_id: UUID
    ) -> UsageReservation:
        with self._connect() as connection:
            prior = self._required(connection, prior_operation_id)
        reservation = self.reserve(prior.role, operation_id, prior.cap)
        if reservation.retry_of is None:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "UPDATE usage_reservations SET retry_of = ? WHERE operation_id = ?",
                    (str(prior_operation_id), str(operation_id)),
                )
            return UsageReservation(
                operation_id=reservation.operation_id,
                role=reservation.role,
                cap=reservation.cap,
                state=reservation.state,
                retry_of=prior_operation_id,
                usage=reservation.usage,
            )
        return reservation

    def reconcile(self, operation_id: UUID, usage: Usage) -> UsageReservation:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self._required(connection, operation_id)
            if not self._fits(usage, reservation.cap):
                raise ValueError("Measured usage exceeds its reservation")
            if reservation.state is ReservationState.RECONCILED:
                if reservation.usage == usage:
                    return reservation
                raise ValueError("Operation was already reconciled differently")
            connection.execute(
                """UPDATE usage_reservations SET state = ?, used_tokens = ?,
                used_seconds = ?, used_spend = ? WHERE operation_id = ?""",
                (
                    ReservationState.RECONCILED.value,
                    usage.tokens,
                    usage.seconds,
                    usage.spend_microusd,
                    str(operation_id),
                ),
            )
            return UsageReservation(
                operation_id=operation_id,
                role=reservation.role,
                cap=reservation.cap,
                state=ReservationState.RECONCILED,
                retry_of=reservation.retry_of,
                usage=usage,
            )

    def cancel(self, operation_id: UUID) -> UsageReservation:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = self._required(connection, operation_id)
            if reservation.state is ReservationState.RESERVED:
                connection.execute(
                    "UPDATE usage_reservations SET state = ? WHERE operation_id = ?",
                    (ReservationState.CANCELLED.value, str(operation_id)),
                )
                return reservation.model_copy(
                    update={"state": ReservationState.CANCELLED}
                )
            return reservation

    def get(self, operation_id: UUID) -> UsageReservation:
        with self._connect() as connection:
            return self._required(connection, operation_id)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _required(
        self, connection: sqlite3.Connection, operation_id: UUID
    ) -> UsageReservation:
        reservation = self._get(connection, operation_id)
        if reservation is None:
            raise ValueError("Reservation does not exist")
        return reservation

    def _get(
        self, connection: sqlite3.Connection, operation_id: UUID
    ) -> UsageReservation | None:
        row: SqlRow | None = connection.execute(
            """SELECT operation_id, role, max_tokens, max_seconds, max_spend, state,
            retry_of, used_tokens, used_seconds, used_spend FROM usage_reservations
            WHERE operation_id = ?""",
            (str(operation_id),),
        ).fetchone()
        if row is None:
            return None
        return self._reservation_from_row(row)

    def _insert(
        self, connection: sqlite3.Connection, reservation: UsageReservation
    ) -> None:
        connection.execute(
            "INSERT INTO usage_reservations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(reservation.operation_id),
                reservation.role.value,
                reservation.cap.max_tokens,
                reservation.cap.max_seconds,
                reservation.cap.max_spend_microusd,
                reservation.state.value,
                None,
                None,
                None,
                None,
            ),
        )

    def _committed_spend(self, connection: sqlite3.Connection) -> int:
        row = connection.execute(
            """SELECT COALESCE(SUM(CASE WHEN state = ? THEN used_spend
            ELSE max_spend END), 0) FROM usage_reservations""",
            (ReservationState.RECONCILED.value,),
        ).fetchone()
        return int(row[0])

    def _reservation_from_row(self, row: SqlRow) -> UsageReservation:
        usage = (
            Usage(
                tokens=self._integer(row[7]),
                seconds=self._integer(row[8]),
                spend_microusd=self._integer(row[9]),
            )
            if row[7] is not None
            else None
        )
        return UsageReservation(
            operation_id=UUID(str(row[0])),
            role=ProductRole(str(row[1])),
            cap=BudgetCap(
                max_tokens=self._integer(row[2]),
                max_seconds=self._integer(row[3]),
                max_spend_microusd=self._integer(row[4]),
            ),
            state=ReservationState(str(row[5])),
            retry_of=UUID(str(row[6])) if row[6] is not None else None,
            usage=usage,
        )

    def _fits(self, usage: Usage, cap: BudgetCap) -> bool:
        return (
            usage.tokens <= cap.max_tokens
            and usage.seconds <= cap.max_seconds
            and usage.spend_microusd <= cap.max_spend_microusd
        )

    def _integer(self, value: SqlValue) -> int:
        if not isinstance(value, int):
            raise ValueError("Ledger contains a malformed numeric value")
        return value
