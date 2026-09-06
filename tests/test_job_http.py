from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from syndicate.controllers.http import create_app
from syndicate.models.jobs import JobStatus, JobSubmission
from syndicate.repositories.jobs import SqliteJobStore


def _client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(SqliteJobStore(tmp_path / "jobs.sqlite")))


def _submit(client: TestClient) -> str:
    created = client.post(
        "/jobs",
        json={"task_ids": ["regex-log", "extract-elf"], "max_iterations": 3},
    )
    if created.status_code != 202:
        raise AssertionError(created.text)
    payload = created.json()
    job_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(job_id, str):
        raise AssertionError("expected job id")
    return job_id


def test_submit_stays_queued_until_worker_runs(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/health").json() == {"status": "ok"}
    job_id = _submit(client)
    body = client.get(f"/jobs/{job_id}").json()
    assert body["status"] == JobStatus.QUEUED.value
    assert body["task_ids"] == ["regex-log", "extract-elf"]
    assert body["trigger_run_id"] is None


def test_list_and_poll_keep_job_queued(tmp_path: Path) -> None:
    client = _client(tmp_path)
    job_id = _submit(client)
    listed = client.get("/jobs")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == job_id
    polled = client.get(f"/jobs/{job_id}")
    assert polled.status_code == 200
    assert polled.json()["status"] == "queued"


def test_cancel_then_rejects_second_cancel(tmp_path: Path) -> None:
    client = _client(tmp_path)
    job_id = _submit(client)
    cancelled = client.post(f"/jobs/{job_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert client.post(f"/jobs/{job_id}/cancel").status_code == 409


def test_unknown_job_is_not_found(tmp_path: Path) -> None:
    client = _client(tmp_path)
    missing = str(uuid4())
    assert client.get(f"/jobs/{missing}").status_code == 404
    assert client.post(f"/jobs/{missing}/cancel").status_code == 404


def test_invalid_submission_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert (
        client.post("/jobs", json={"task_ids": ["regex-log", "regex-log"]}).status_code
        == 422
    )
    JobSubmission(task_ids=("ok",))
