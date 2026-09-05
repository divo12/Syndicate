# AHE seed preparation — M1-T1-S1 / P06

Source: https://github.com/china-qijizhifeng/agentic-harness-engineering/tree/8b2a55d97590363fe50c3cc6b5e833b020a4bb4c/agents/code_agent_simple

`harnesses/seed` preserves the original prompt, two memory templates, agent YAML,
and tool schema byte-for-byte. LICENSE is copied from the upstream repository
root. `SEED_ARTIFACTS` records their SHA-256 pins. The manifest also identifies the
upstream shell implementation hash and NexAU v0.3.9 commit; those dependencies
are references, not installed or executed by P06.

`prepare_baseline(seed_dir, framework_lock, model)` rejects altered/unrecorded
files and symlinks, then returns an immutable nonsecret `BaselineManifest`.
Its identity covers exact assets, source revisions, lock bytes and GPT-5.4-mini
model settings. Serialization supports saving a preparation receipt.

Compatibility diff at this stage is empty: no source text has been adapted.
The original YAML is **provenance only**, not a runnable product configuration:
its LLM environment placeholders, 200K context, 32K output, sampling settings,
300 iterations and original shell import must not bypass admitted runtime settings.
P07 supplies sandbox shell behavior; the runner must record its explicit runtime
compatibility diff and verified limits before sealing H0 (BASE-01/BASE-02).
The prepared identity is not a final H0 hash or evidence of a model-backed run.
