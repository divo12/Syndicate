# Benchmark checkout

`ITSMBench/` is the full upstream Git submodule, pinned to
`30da7457d5479d0bcfae40dece7bd85d66df4401`. Its source and license stay intact.
Initialize it after cloning Syndicate:

```sh
git submodule update --init benchmark/ITSMBench
```

The checkout stores tasks; `syndicate.repositories.benchmark_manifest` validates their provenance,
metadata, and operator-declared splits. It does not run the benchmark or choose splits.
From the Syndicate repository root:

```python
from pathlib import Path
from syndicate.repositories.benchmark_manifest import (
    Assignment, BenchmarkManifest, ITSMBENCH_REVISION, Split,
)

manifest = BenchmarkManifest.load(
    Path("benchmark/ITSMBench"),
    ITSMBENCH_REVISION,
    (Assignment("task-a-1", Split.DEVELOPMENT, "a"),),
)
inputs = manifest.public_inputs(Split.DEVELOPMENT)
```

The controller retains the manifest and its hash. Agents receive only `inputs`:
instructions and provenance, without solutions or verifier content. Final-test
projection is blocked. Tasks in the same family cannot cross splits.

Keep this entire checkout outside agent-accessible mounts. The adapter filters
its output; it does not sandbox filesystem access. Store run output elsewhere:
the loader rejects a modified checkout, including untracked or ignored files.

CI initializes the submodule and tests the real pinned task metadata alongside
synthetic rejection cases. Updating the benchmark requires changing both the
submodule commit and `ITSMBENCH_REVISION`, then reviewing and testing that change.
