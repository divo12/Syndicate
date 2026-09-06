# Offline preflight

From the project root, run:

```sh
.venv/bin/python -m syndicate.cli preflight --config campaign.json
```

The operator supplies ordinary campaign settings. The controller creates the
request IDs, approval hashes, and output directory. No model calls or benchmark
execution occur: a successful result means offline configuration is valid.

For example, save this as `campaign.json` beside the `benchmark/` directory.
These small budget values are illustrative; set the limits you intend to allow.

```json
{
  "env_file": ".env",
  "benchmark_root": "benchmark/ITSMBench",
  "assignments": [
    {"task_id": "task-a-1", "split": "development", "family": "a"}
  ],
  "budget": {
    "campaign_cap": {
      "max_tokens": 1000, "max_seconds": 60, "max_spend_microusd": 10000
    },
    "role_budgets": [
      {"role": "executor", "cap": {
        "max_tokens": 1000, "max_seconds": 60, "max_spend_microusd": 10000
      }},
      {"role": "judge_builder", "cap": {
        "max_tokens": 1000, "max_seconds": 60, "max_spend_microusd": 10000
      }},
      {"role": "task_judge", "cap": {
        "max_tokens": 1000, "max_seconds": 60, "max_spend_microusd": 10000
      }},
      {"role": "improvement_agent", "cap": {
        "max_tokens": 1000, "max_seconds": 60, "max_spend_microusd": 10000
      }}
    ]
  }
}
```

Relative input paths resolve against `campaign.json`'s directory. Configure the
credential file as described in [model configuration](model-config.md), and
initialize the pinned benchmark checkout before running preflight. The config
file is trusted operator input; it must not be supplied by an evaluated agent.

Each invocation creates a fresh directory under the current working directory:

```text
.syndicate/runs/<operation-id>/<attempt-id>/
    request.json       Generated request
    controller.json    Approved configuration snapshot, without API credentials
    preflight.json     Validated offline result
```

One JSON receipt on stdout identifies the run and the result's SHA-256 digest.
`live_model_verified` remains false. Exit codes are 0 for success, 2 for invalid
configuration or invocation, and 1 for filesystem/infrastructure failures. Errors
omit configuration values. Existing runs are preserved; a failed write may leave
an incomplete run directory, which has no successful terminal receipt.

The command validates pinned source, split assignments, and nonsecret settings
before admission, then rechecks them during execution. It never updates the
shared `.syndicate/controller.json` anchor used by internal callers. Keep both
controller snapshots and the full benchmark outside agent-accessible mounts.

The existing `execute --request ABSOLUTE_REQUEST_JSON` transport remains for
controller-managed requests. It requires its separately provisioned shared trust
anchor and retains its original receipt and exit-code contract. It does not
automatically trust snapshots found beside a request.
