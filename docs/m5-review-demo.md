# M5 review demo runbook

This is a preparation runbook. It uses only typed synthetic fixtures until the
M4 gate has produced reviewed recorded receipts. It makes no claim about model,
benchmark, held-out, cost, or recovery results.

## Preconditions

- Check out the stacked M5 tip through P29.
- Keep M4 PRs 33, 35, 36, 38, and 39 as the typed source for paired schedules,
  assessments, and promotion lineage.
- Do not export Neatlogs spans locally. The console displays only remote trace
  and span IDs already contained in validated citations.

## Scenario 1: repeat the read-only path

Run:

```sh
uv run --extra dev pytest tests/test_review_console.py -q
```

The test fixture is labeled `Synthetic preparation data`. It covers the path
from finding to remote evidence ID, diagnosis, candidate diff and M4 assessment.
Repeat the exact command to verify deterministic local rendering and
serialization; this is not an experiment repeat.

## Scenario 2: demonstrate an incomplete comparison safely

The synthetic delivery report uses the M4 `inconclusive` decision with held-out
status `not_run`. Inspect it through the same focused test. The expected display
is an explicit limitation, not a score, cost, promotion, or held-out outcome.

If a recorded comparison is incomplete, preserve its M4 decision/reason code in
the report and stop. Do not infer a winner or replace missing evidence with a
synthetic value.

## Scenario 3: recovery after M4 data arrives

1. Validate the controller-provided M4 receipt through its typed contract.
2. Supply its typed `PairSchedule` and `ComparisonAssessment` to the P28/P29
   views; retain Neatlogs references only.
3. Re-run the focused tests and the full quality gate:

   ```sh
   uv run --extra dev python scripts/quality.py
   ```

4. Record the actual M4 integration outcome separately. M5 presentation tests
   do not convert that outcome into acceptance evidence.

## Stop conditions

- A missing or incomplete remote citation blocks the corresponding navigation.
- Missing held-out evidence remains `not_run` or `blocked`; it is never zero.
- A failed quality gate blocks the demo until repaired and rerun.
