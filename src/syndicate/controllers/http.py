"""Single-tenant job API. Trigger.dev owns cloud execution; this store is durable."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from syndicate.adapters.trigger_jobs import trigger_from_env
from syndicate.models.jobs import Job, JobStatus, JobSubmission
from syndicate.repositories.jobs import JobStore, MemoryJobStore, PostgresJobStore
from syndicate.services.job_worker import JobWorker


def _payload(job: Job, store: JobStore) -> dict[str, object]:
    body = job.model_dump(mode="json")
    body["iterations"] = [
        item.model_dump(mode="json") for item in store.iterations(job.id)
    ]
    return body


def create_app(store: JobStore, worker: JobWorker | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if worker is None:
            yield
            return
        stopped = asyncio.Event()

        async def ticks() -> None:
            while not stopped.is_set():
                worker.process_one()
                await asyncio.sleep(0.5)

        task = asyncio.create_task(ticks())
        yield
        stopped.set()
        task.cancel()

    app = FastAPI(title="Syndicate", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/jobs", status_code=202)
    def submit(submission: JobSubmission) -> JSONResponse:
        return JSONResponse(
            status_code=202, content=_payload(store.create(submission), store)
        )

    @app.get("/jobs")
    def list_jobs(status: JobStatus | None = None) -> list[dict[str, object]]:
        return [_payload(job, store) for job in store.list_jobs(status)]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: UUID) -> dict[str, object]:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _payload(job, store)

    @app.post("/jobs/{job_id}/cancel")
    def cancel_job(job_id: UUID) -> dict[str, object]:
        if store.get(job_id) is None:
            raise HTTPException(status_code=404, detail="job not found")
        job = store.cancel(job_id)
        if job is None:
            raise HTTPException(status_code=409, detail="job is not cancellable")
        return _payload(job, store)

    return app


def app_from_env() -> FastAPI:
    url = os.environ.get("DATABASE_URL")
    store: JobStore
    store = MemoryJobStore() if url is None else PostgresJobStore(url)
    worker = JobWorker(
        store,
        trigger_from_env(
            os.environ.get("TRIGGER_API_URL"), os.environ.get("TRIGGER_SECRET_KEY")
        ),
        lineage_root=Path(os.environ.get("LINEAGE_ROOT", ".syndicate/lineage")),
    )
    return create_app(store, worker)


app = app_from_env()
