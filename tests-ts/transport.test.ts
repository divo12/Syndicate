import assert from "node:assert/strict";
import { chmod, mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import { TransportError, invokePython } from "../trigger/transport.js";

const OPERATION_ID = "11111111-1111-4111-8111-111111111111";
const ATTEMPT_ID = "22222222-2222-4222-8222-222222222222";

async function fixture(mode: string): Promise<{
  controllerCwd: string;
  pythonExecutable: string;
  requestPath: string;
}> {
  const controllerCwd = await mkdtemp(join(tmpdir(), "syndicate-trigger-"));
  const run = join(
    controllerCwd,
    ".syndicate",
    "runs",
    OPERATION_ID,
    ATTEMPT_ID,
  );
  await mkdir(run, { recursive: true });
  const requestPath = join(run, "request.json");
  await writeFile(requestPath, "{}\n");
  const pythonExecutable = join(controllerCwd, "python");
  await writeFile(
    pythonExecutable,
    `#!/usr/bin/env node
const mode = ${JSON.stringify(mode)};
const receipt = ${JSON.stringify({
  schema_version: 1,
  operation_id: OPERATION_ID,
  attempt_id: ATTEMPT_ID,
  status: "completed",
  artifact_refs: [],
})};
if (mode === "slow") setInterval(() => {}, 1_000);
if (mode === "multiple") console.log(JSON.stringify(receipt), JSON.stringify(receipt));
if (mode === "ok") { console.error("controller log"); console.log(JSON.stringify(receipt)); }
`,
  );
  await chmod(pythonExecutable, 0o755);
  return { controllerCwd, pythonExecutable, requestPath };
}

test("invokes the pinned CLI with one matching receipt and preserves stderr logs", async () => {
  const input = await fixture("ok");
  const result = await invokePython({
    ...input,
    operationId: OPERATION_ID,
    attemptId: ATTEMPT_ID,
    timeoutMs: 1_000,
    environment: { PATH: process.env.PATH ?? "" },
  });
  assert.equal(result.receipt.status, "completed");
  assert.equal(result.stderr, "controller log\n");
});

test("rejects multiple stdout receipts", async () => {
  const input = await fixture("multiple");
  await assert.rejects(
    invokePython({
      ...input,
      operationId: OPERATION_ID,
      attemptId: ATTEMPT_ID,
      timeoutMs: 1_000,
      environment: { PATH: process.env.PATH ?? "" },
    }),
    TransportError,
  );
});

test("terminates an owned process group at its deadline", async () => {
  const input = await fixture("slow");
  await assert.rejects(
    invokePython({
      ...input,
      operationId: OPERATION_ID,
      attemptId: ATTEMPT_ID,
      timeoutMs: 25,
      environment: { PATH: process.env.PATH ?? "" },
    }),
    /deadline/,
  );
});

test("cancels an already-aborted invocation", async () => {
  const input = await fixture("slow");
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(
    invokePython({
      ...input,
      operationId: OPERATION_ID,
      attemptId: ATTEMPT_ID,
      timeoutMs: 1_000,
      environment: { PATH: process.env.PATH ?? "" },
      signal: controller.signal,
    }),
    /cancellation/,
  );
});

test("refuses a request outside its controller-owned run directory", async () => {
  const input = await fixture("ok");
  const requestPath = join(input.controllerCwd, "outside.json");
  await writeFile(requestPath, "{}\n");
  await assert.rejects(
    invokePython({
      ...input,
      requestPath,
      operationId: OPERATION_ID,
      attemptId: ATTEMPT_ID,
      timeoutMs: 1_000,
      environment: { PATH: process.env.PATH ?? "" },
    }),
    /controller-owned/,
  );
});
