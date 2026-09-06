from uuid import uuid4

from fastapi.testclient import TestClient

from syndicate.controllers.http import create_app
from syndicate.models.jobs import JobStatus, JobSubmission
from syndicate.repositories.jobs import MemoryJobStore


def _client() -> TestClient:
    return TestClient(create_app(MemoryJobStore()))


def _submit(client: TestClient) -> dict[str, object]:
    created = client.post(
        "/jobs",
        json={"task_ids": ["regex-log", "extract-elf"], "max_iterations": 3},
    )
    if created.status_code != 202:
        raise AssertionError(created.text)
    return created.json()


def test_submit_stays_queued_until_worker_runs() -> None:
    client = _client()
    assert client.get("/health").json() == {"status": "ok"}
    body = _submit(client)
    assert body["status"] == JobStatus.QUEUED.value
    assert body["task_ids"] == ["regex-log", "extract-elf"]
    assert body["trigger_run_id"] is None


def test_list_and_poll_keep_job_queued() -> None:
    client = _client()
    body = _submit(client)
    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == body["id"]
    polled = client.get(f"/jobs/{body['id']}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "queued"


def test_cancel_then_rejects_second_cancel() -> None:
    client = _client()
    body = _submit(client)
    cancelled = client.post(f"/jobs/{body['id']}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post(f"/jobs/{body['id']}/cancel").status_code == 409


def test_unknown_job_is_not_found() -> None:
    client = _client()
    missing = str(uuid4())
    assert client.get(f"/jobs/{missing}").status_code == 404
    assert client.post(f"/jobs/{missing}/cancel").status_code == 404


def test_invalid_submission_is_rejected() -> None:
    client = _client()
    assert (
        client.post("/jobs", json={"task_ids": ["regex-log", "regex-log"]}).status_code
        == 422
    )
    JobSubmission(task_ids=("ok",))
