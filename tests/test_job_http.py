from uuid import uuid4

from fastapi.testclient import TestClient

from syndicate.controllers.http import create_app
from syndicate.models.jobs import JobStatus, JobSubmission
from syndicate.repositories.jobs import MemoryJobStore


def test_submit_poll_and_cancel_keep_jobs_queued_until_cancel() -> None:
    store = MemoryJobStore()
    client = TestClient(create_app(store))
    assert client.get("/health").json() == {"status": "ok"}

    created = client.post(
        "/jobs",
        json={"task_ids": ["regex-log", "extract-elf"], "max_iterations": 3},
    )
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == JobStatus.QUEUED.value
    assert body["task_ids"] == ["regex-log", "extract-elf"]
    assert body["trigger_run_id"] is None

    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == body["id"]

    polled = client.get(f"/jobs/{body['id']}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "queued"

    cancelled = client.post(f"/jobs/{body['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post(f"/jobs/{body['id']}/cancel").status_code == 409


def test_unknown_job_is_not_found() -> None:
    client = TestClient(create_app(MemoryJobStore()))
    missing = str(uuid4())
    assert client.get(f"/jobs/{missing}").status_code == 404
    assert client.post(f"/jobs/{missing}/cancel").status_code == 404


def test_invalid_submission_is_rejected() -> None:
    client = TestClient(create_app(MemoryJobStore()))
    assert (
        client.post("/jobs", json={"task_ids": ["regex-log", "regex-log"]}).status_code
        == 422
    )
    JobSubmission(task_ids=("ok",))
