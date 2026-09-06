# Operator: submit tasks and run the learning loop

Single-tenant job API. `DATABASE_URL` is required (Postgres in Compose, SQLite for tests). Trigger.dev runs the cloud loop when `TRIGGER_SECRET_KEY` is set. Without that key the API worker still runs the simulated loop locally so `make up` works.

## Start

```bash
cp .env.example .env
make up
```

Optional Trigger dashboard:

```bash
npx trigger.dev@latest dev
```

## Submit one or many tasks

```bash
make client
python3 scripts/job_client.py regex-log
python3 scripts/job_client.py regex-log extract-elf --max-iterations 3
python3 scripts/job_client.py task-a-1 --executor harbor
```

`POST /jobs` returns `202` with a job id. Poll `GET /jobs/{id}` for status, scores, and iterations.

## Executor

- `simulated` (default): last baseline task fails, generation 1 passes
- `harbor`: requires `E2B_API_KEY` plus Azure keys in `SYNDICATE_ENV_FILE`; uses live handlers. Task ids must match `task-*` (for example `task-a-1`). Set `SYNDICATE_HARBOR_STUB=1` to keep the simulated Harbor stand-in.

Trigger `run-trial` calls `python -m syndicate.cli trial` when `SYNDICATE_PYTHON` is set; otherwise it uses the in-process simulator.
