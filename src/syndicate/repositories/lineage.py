"""SQLite compare-and-swap for the accepted harness pointer."""

import sqlite3
from pathlib import Path
from uuid import UUID

from syndicate.models.lineage import (
    HarnessVersion,
    PromotionReceipt,
    PromotionStatus,
)


class HarnessLineage:
    """Serialize accepted-version mutation through SQLite compare-and-swap."""

    def __init__(
        self, path: Path, initial_harness_hash: str, initial_memory_hash: str
    ) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        initial = HarnessVersion(
            harness_hash=initial_harness_hash, memory_hash=initial_memory_hash
        )
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS incumbent (id INTEGER PRIMARY KEY
                CHECK (id = 1), harness_hash TEXT NOT NULL,
                memory_hash TEXT NOT NULL)"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS lineage (operation_id TEXT PRIMARY KEY,
                status TEXT NOT NULL, previous_harness TEXT NOT NULL,
                previous_memory TEXT NOT NULL, current_harness TEXT NOT NULL,
                current_memory TEXT NOT NULL)"""
            )
            connection.execute(
                "INSERT OR IGNORE INTO incumbent VALUES (1, ?, ?)",
                (initial.harness_hash, initial.memory_hash),
            )

    def current(self) -> HarnessVersion:
        with self._connect() as connection:
            return self._current(connection)

    def promote(
        self,
        operation_id: UUID,
        parent_harness_hash: str,
        candidate_harness_hash: str,
        candidate_memory_hash: str,
    ) -> PromotionReceipt:
        candidate = HarnessVersion(
            harness_hash=candidate_harness_hash, memory_hash=candidate_memory_hash
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._current(connection)
            if previous.harness_hash != parent_harness_hash:
                return PromotionReceipt(
                    operation_id=operation_id,
                    status=PromotionStatus.STALE,
                    previous=previous,
                    current=previous,
                )
            self._set_current(connection, candidate)
            self._record(
                connection, operation_id, PromotionStatus.PROMOTED, previous, candidate
            )
            return PromotionReceipt(
                operation_id=operation_id,
                status=PromotionStatus.PROMOTED,
                previous=previous,
                current=candidate,
            )

    def rollback(self, operation_id: UUID, harness_hash: str) -> PromotionReceipt:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._current(connection)
            target = self._version_by_harness(connection, harness_hash)
            self._set_current(connection, target)
            self._record(
                connection, operation_id, PromotionStatus.ROLLED_BACK, previous, target
            )
            return PromotionReceipt(
                operation_id=operation_id,
                status=PromotionStatus.ROLLED_BACK,
                previous=previous,
                current=target,
            )

    def history(self) -> tuple[PromotionReceipt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT operation_id, status, previous_harness, previous_memory,
                current_harness, current_memory FROM lineage ORDER BY rowid"""
            ).fetchall()
        return tuple(self._receipt(row) for row in rows)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def _current(self, connection: sqlite3.Connection) -> HarnessVersion:
        row = connection.execute(
            "SELECT harness_hash, memory_hash FROM incumbent"
        ).fetchone()
        if row is None:
            raise RuntimeError("Incumbent pointer is missing")
        return HarnessVersion(harness_hash=str(row[0]), memory_hash=str(row[1]))

    def _set_current(
        self, connection: sqlite3.Connection, version: HarnessVersion
    ) -> None:
        connection.execute(
            "UPDATE incumbent SET harness_hash = ?, memory_hash = ? WHERE id = 1",
            (version.harness_hash, version.memory_hash),
        )

    def _record(
        self,
        connection: sqlite3.Connection,
        operation_id: UUID,
        status: PromotionStatus,
        previous: HarnessVersion,
        current: HarnessVersion,
    ) -> None:
        connection.execute(
            "INSERT INTO lineage VALUES (?, ?, ?, ?, ?, ?)",
            (
                str(operation_id),
                status.value,
                previous.harness_hash,
                previous.memory_hash,
                current.harness_hash,
                current.memory_hash,
            ),
        )

    def _version_by_harness(
        self, connection: sqlite3.Connection, harness_hash: str
    ) -> HarnessVersion:
        row = connection.execute(
            """SELECT previous_harness, previous_memory FROM lineage
            WHERE previous_harness = ? UNION SELECT current_harness, current_memory
            FROM lineage WHERE current_harness = ? LIMIT 1""",
            (harness_hash, harness_hash),
        ).fetchone()
        if row is None:
            raise ValueError("Rollback target is not accepted lineage")
        return HarnessVersion(harness_hash=str(row[0]), memory_hash=str(row[1]))

    def _receipt(self, row: tuple[str, str, str, str, str, str]) -> PromotionReceipt:
        return PromotionReceipt(
            operation_id=UUID(row[0]),
            status=PromotionStatus(row[1]),
            previous=HarnessVersion(harness_hash=row[2], memory_hash=row[3]),
            current=HarnessVersion(harness_hash=row[4], memory_hash=row[5]),
        )
