"""Submit one or many tasks and poll until the learning loop stops."""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request


def _request(method: str, url: str, body: dict[str, object] | None = None) -> object:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        raise SystemExit(error.read().decode()) from error


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Submit Syndicate learning-loop jobs")
    parser.add_argument("task_ids", nargs="+")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--max-iterations", type=int, default=5)
    parser.add_argument("--patience", type=int, default=2)
    parser.add_argument("--executor", default="simulated")
    parsed = parser.parse_args(arguments)
    created = _request(
        "POST",
        f"{parsed.base_url}/jobs",
        {
            "task_ids": parsed.task_ids,
            "max_iterations": parsed.max_iterations,
            "patience": parsed.patience,
            "executor": parsed.executor,
        },
    )
    if not isinstance(created, dict):
        raise SystemExit("invalid submit response")
    job_id = created["id"]
    print(f"submitted {job_id} status={created['status']}")
    while True:
        job = _request("GET", f"{parsed.base_url}/jobs/{job_id}")
        if not isinstance(job, dict):
            raise SystemExit("invalid poll response")
        print(
            f"  status={job['status']} best_score={job.get('best_score')} "
            f"iterations={len(job.get('iterations') or [])}"
        )
        if job["status"] not in {"queued", "running"}:
            print(json.dumps(job, indent=2, sort_keys=True))
            return terminal_exit(str(job["status"]))
        time.sleep(1)


def terminal_exit(status: str) -> int:
    return 0 if status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
