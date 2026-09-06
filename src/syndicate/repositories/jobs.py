"""Job persistence. SQLite for tests; Postgres for the API container."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import psycopg

from syndicate.models.jobs import (
    ExecutorKind,
    Iteration,
    IterationPhase,
    Job,
    JobStatus,
    JobSubmission,
    StopReason,
    TaskOutcome,
    TaskResult,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    task_ids TEXT NOT NULL,
    max_iterations INTEGER NOT NULL,
    patience INTEGER NOT NULL,
    executor TEXT NOT NULL,
    trigger_run_id TEXT,
    best_score DOUBLE PRECISION,
    stop_reason TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS iterations (
    job_id TEXT NOT NULL,
    number INTEGER NOT NULL,
    phase TEXT NOT NULL,
    generation INTEGER NOT NULL,
    score DOUBLE PRECISION,
    accepted BOOLEAN,
    results TEXT NOT NULL,
    PRIMARY KEY (job_id, number)
)
"""


class JobStore(Protocol):
    def create(self, submission: JobSubmission) -> Job: ...
    def get(self, job_id: UUID) -> Job | None: ...
    def list_jobs(self, status: JobStatus | None = None) -> tuple[Job, ...]: ...
    def cancel(self, job_id: UUID) -> Job | None: ...
    def claim(self) -> Job | None: ...
    def attach_trigger(self, job_id: UUID, run_id: str) -> Job | None: ...
    def save_iteration(self, iteration: Iteration) -> None: ...
    def iterations(self, job_id: UUID) -> tuple[Iteration, ...]: ...
    def finish(
        self,
        job_id: UUID,
        status: JobStatus,
        stop_reason: StopReason,
        best_score: float,
        error: str | None = None,
    ) -> Job | None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _cancellable(job: Job | None) -> bool:
    return job is not None and job.status in (JobStatus.QUEUED, JobStatus.RUNNING)


def _new_job(submission: JobSubmission) -> Job:
    stamp = _now()
    return Job(
        id=uuid4(),
        status=JobStatus.QUEUED,
        task_ids=submission.task_ids,
        max_iterations=submission.max_iterations,
        patience=submission.patience,
        executor=submission.executor,
        created_at=stamp,
        updated_at=stamp,
    )


def _row(values: tuple[object, ...]) -> Job:
    stop = values[8]
    return Job(
        id=UUID(str(values[0])),
        status=JobStatus(str(values[1])),
        task_ids=tuple(json.loads(str(values[2]))),
        max_iterations=int(str(values[3])),
        patience=int(str(values[4])),
        executor=ExecutorKind(str(values[5])),
        trigger_run_id=str(values[6]) if values[6] is not None else None,
        best_score=float(str(values[7])) if values[7] is not None else None,
        stop_reason=StopReason(str(stop)) if stop is not None else None,
        error=str(values[9]) if values[9] is not None else None,
        created_at=datetime.fromisoformat(str(values[10])),
        updated_at=datetime.fromisoformat(str(values[11])),
    )


def _iteration_row(values: tuple[object, ...]) -> Iteration:
    raw = json.loads(str(values[6]))
    results = tuple(
        TaskResult(
            task_id=str(item["task_id"]),
            outcome=TaskOutcome(str(item["outcome"])),
            reward=float(item["reward"]),
        )
        for item in raw
    )
    accepted = values[5]
    return Iteration(
        job_id=UUID(str(values[0])),
        number=int(str(values[1])),
        phase=IterationPhase(str(values[2])),
        generation=int(str(values[3])),
        score=None if values[4] is None else float(str(values[4])),
        accepted=None if accepted is None else bool(accepted),
        results=results,
    )


class SqliteJobStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        with self._connect() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    def create(self, submission: JobSubmission) -> Job:
        job = _new_job(submission)
        self._insert(job)
        return job

    def _insert(self, job: Job) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                _job_values(job),
            )
            connection.commit()

    def get(self, job_id: UUID) -> Job | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (str(job_id),)
            ).fetchone()
        return None if row is None else _row(tuple(row))

    def list_jobs(self, status: JobStatus | None = None) -> tuple[Job, ...]:
        query = "SELECT * FROM jobs ORDER BY created_at"
        args: tuple[str, ...] = ()
        if status is not None:
            query = "SELECT * FROM jobs WHERE status = ? ORDER BY created_at"
            args = (status.value,)
        with self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
        return tuple(_row(tuple(row)) for row in rows)

    def cancel(self, job_id: UUID) -> Job | None:
        if not _cancellable(self.get(job_id)):
            return None
        with self._connect() as connection:
            connection.execute(
                """UPDATE jobs SET status = ?, stop_reason = ?, updated_at = ?
                WHERE id = ? AND status IN (?, ?)""",
                (
                    JobStatus.CANCELLED.value,
                    StopReason.CANCELLED.value,
                    _now().isoformat(),
                    str(job_id),
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                ),
            )
            connection.commit()
        return self.get(job_id)

    def claim(self) -> Job | None:
        stamp = _now().isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM jobs WHERE status = ?
                ORDER BY created_at LIMIT 1""",
                (JobStatus.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (JobStatus.RUNNING.value, stamp, row[0]),
            )
            connection.commit()
        return self.get(UUID(str(row[0])))

    def attach_trigger(self, job_id: UUID, run_id: str) -> Job | None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET trigger_run_id = ?, updated_at = ? WHERE id = ?",
                (run_id, _now().isoformat(), str(job_id)),
            )
            connection.commit()
        return self.get(job_id)

    def save_iteration(self, iteration: Iteration) -> None:
        payload = json.dumps(
            [item.model_dump(mode="json") for item in iteration.results]
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO iterations VALUES (?,?,?,?,?,?,?)",
                (
                    str(iteration.job_id),
                    iteration.number,
                    iteration.phase.value,
                    iteration.generation,
                    iteration.score,
                    iteration.accepted,
                    payload,
                ),
            )
            connection.commit()

    def iterations(self, job_id: UUID) -> tuple[Iteration, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM iterations WHERE job_id = ? ORDER BY number",
                (str(job_id),),
            ).fetchall()
        return tuple(_iteration_row(tuple(row)) for row in rows)

    def finish(
        self,
        job_id: UUID,
        status: JobStatus,
        stop_reason: StopReason,
        best_score: float,
        error: str | None = None,
    ) -> Job | None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE jobs SET status = ?, stop_reason = ?, best_score = ?,
                error = ?, updated_at = ? WHERE id = ? AND status = ?""",
                (
                    status.value,
                    stop_reason.value,
                    best_score,
                    error,
                    _now().isoformat(),
                    str(job_id),
                    JobStatus.RUNNING.value,
                ),
            )
            connection.commit()
        return self.get(job_id)


def _job_values(job: Job) -> tuple[object, ...]:
    return (
        str(job.id),
        job.status.value,
        json.dumps(job.task_ids),
        job.max_iterations,
        job.patience,
        job.executor.value,
        job.trigger_run_id,
        job.best_score,
        None if job.stop_reason is None else job.stop_reason.value,
        job.error,
        job.created_at.isoformat(),
        job.updated_at.isoformat(),
    )


class PostgresJobStore:
    def __init__(self, database_url: str) -> None:
        self._url = database_url
        with psycopg.connect(database_url) as connection:
            connection.execute(SCHEMA)
            connection.commit()

    def create(self, submission: JobSubmission) -> Job:
        job = _new_job(submission)
        self._insert(job)
        return job

    def _insert(self, job: Job) -> None:
        with psycopg.connect(self._url) as connection:
            connection.execute(
                """INSERT INTO jobs VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )""",
                (
                    str(job.id),
                    job.status.value,
                    json.dumps(job.task_ids),
                    job.max_iterations,
                    job.patience,
                    job.executor.value,
                    job.trigger_run_id,
                    job.best_score,
                    None if job.stop_reason is None else job.stop_reason.value,
                    job.error,
                    job.created_at.isoformat(),
                    job.updated_at.isoformat(),
                ),
            )
            connection.commit()

    def get(self, job_id: UUID) -> Job | None:
        with psycopg.connect(self._url) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = %s", (str(job_id),)
            ).fetchone()
        return None if row is None else _row(tuple(row))

    def list_jobs(self, status: JobStatus | None = None) -> tuple[Job, ...]:
        query = "SELECT * FROM jobs ORDER BY created_at"
        args: tuple[str, ...] = ()
        if status is not None:
            query = "SELECT * FROM jobs WHERE status = %s ORDER BY created_at"
            args = (status.value,)
        with psycopg.connect(self._url) as connection:
            rows = connection.execute(query, args).fetchall()
        return tuple(_row(tuple(row)) for row in rows)

    def cancel(self, job_id: UUID) -> Job | None:
        if not _cancellable(self.get(job_id)):
            return None
        stamp = _now().isoformat()
        with psycopg.connect(self._url) as connection:
            connection.execute(
                """UPDATE jobs SET status = %s, stop_reason = %s, updated_at = %s
                WHERE id = %s AND status IN (%s, %s)""",
                (
                    JobStatus.CANCELLED.value,
                    StopReason.CANCELLED.value,
                    stamp,
                    str(job_id),
                    JobStatus.QUEUED.value,
                    JobStatus.RUNNING.value,
                ),
            )
            connection.commit()
        return self.get(job_id)

    def claim(self) -> Job | None:
        stamp = _now().isoformat()
        with psycopg.connect(self._url) as connection:
            row = connection.execute(
                """UPDATE jobs SET status = %s, updated_at = %s
                WHERE id = (
                    SELECT id FROM jobs WHERE status = %s
                    ORDER BY created_at LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING *""",
                (JobStatus.RUNNING.value, stamp, JobStatus.QUEUED.value),
            ).fetchone()
            connection.commit()
        return None if row is None else _row(tuple(row))

    def attach_trigger(self, job_id: UUID, run_id: str) -> Job | None:
        with psycopg.connect(self._url) as connection:
            connection.execute(
                "UPDATE jobs SET trigger_run_id = %s, updated_at = %s WHERE id = %s",
                (run_id, _now().isoformat(), str(job_id)),
            )
            connection.commit()
        return self.get(job_id)

    def save_iteration(self, iteration: Iteration) -> None:
        payload = json.dumps(
            [item.model_dump(mode="json") for item in iteration.results]
        )
        with psycopg.connect(self._url) as connection:
            connection.execute(
                """INSERT INTO iterations VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    str(iteration.job_id),
                    iteration.number,
                    iteration.phase.value,
                    iteration.generation,
                    iteration.score,
                    iteration.accepted,
                    payload,
                ),
            )
            connection.commit()

    def iterations(self, job_id: UUID) -> tuple[Iteration, ...]:
        with psycopg.connect(self._url) as connection:
            rows = connection.execute(
                "SELECT * FROM iterations WHERE job_id = %s ORDER BY number",
                (str(job_id),),
            ).fetchall()
        return tuple(_iteration_row(tuple(row)) for row in rows)

    def finish(
        self,
        job_id: UUID,
        status: JobStatus,
        stop_reason: StopReason,
        best_score: float,
        error: str | None = None,
    ) -> Job | None:
        with psycopg.connect(self._url) as connection:
            connection.execute(
                """UPDATE jobs SET status = %s, stop_reason = %s, best_score = %s,
                error = %s, updated_at = %s WHERE id = %s AND status = %s""",
                (
                    status.value,
                    stop_reason.value,
                    best_score,
                    error,
                    _now().isoformat(),
                    str(job_id),
                    JobStatus.RUNNING.value,
                ),
            )
            connection.commit()
        return self.get(job_id)
