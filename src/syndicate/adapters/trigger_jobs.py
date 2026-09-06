"""Fire the Trigger.dev learning-loop task. Missing credentials skip dispatch."""

from typing import Protocol

import httpx

from syndicate.models.jobs import Job


class TriggerLoop(Protocol):
    def start_loop(self, job: Job) -> str | None: ...


class NullTriggerLoop:
    def start_loop(self, job: Job) -> str | None:
        del job
        return None


class HttpTriggerLoop:
    def __init__(self, api_url: str, secret_key: str) -> None:
        if not secret_key.strip():
            raise ValueError("TRIGGER_SECRET_KEY is required")
        self._api_url = api_url.rstrip("/")
        self._secret_key = secret_key

    def start_loop(self, job: Job) -> str | None:
        response = httpx.post(
            f"{self._api_url}/api/v1/tasks/learning-loop/trigger",
            headers={"Authorization": f"Bearer {self._secret_key}"},
            json={
                "payload": {
                    "taskIds": list(job.task_ids),
                    "maxIterations": job.max_iterations,
                    "patience": job.patience,
                }
            },
            timeout=15.0,
        )
        response.raise_for_status()
        run_id = response.json().get("id")
        return str(run_id) if run_id else None


def trigger_from_env(api_url: str | None, secret_key: str | None) -> TriggerLoop:
    if secret_key is None or secret_key.strip() == "":
        return NullTriggerLoop()
    return HttpTriggerLoop(api_url or "https://api.trigger.dev", secret_key)
