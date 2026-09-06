import assert from "node:assert/strict";
import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

import type {
  CommandReceipt,
  PythonInvocation,
  TransportResult,
} from "../trigger/transport.js";
import { WorkflowError, runWorkflow } from "../trigger/workflow.js";

const IDS = [
  "11111111-1111-4111-8111-111111111111",
  "22222222-2222-4222-8222-222222222222",
  "33333333-3333-4333-8333-333333333333",
  "44444444-4444-4444-8444-444444444444",
  "55555555-5555-4555-8555-555555555555",
  "66666666-6666-4666-8666-666666666666",
] as const;

async function controller(): Promise<string> {
  const cwd = await mkdtemp(join(tmpdir(), "syndicate-workflow-"));
  const schemas = join(cwd, ".syndicate", "schemas");
  await mkdir(schemas, { recursive: true });
  await writeFile(join(schemas, "command-request-v1.json"), "{}");
  await writeFile(join(schemas, "command-receipt-v1.json"), "{}");
  return cwd;
}

function invocation(controllerCwd: string, operationId: string): PythonInvocation {
  return {
    pythonExecutable: "/pinned/python",
    controllerCwd,
    requestPath: `${controllerCwd}/.syndicate/runs/${operationId}/${operationId}/request.json`,
    operationId,
    attemptId: operationId,
    timeoutMs: 1_000,
    environment: {},
  };
}

function receipt(
  input: PythonInvocation,
  status: CommandReceipt["status"],
): TransportResult {
  return {
    status: status === "completed" ? "success" : "warning",
    summary: `Python command returned ${status}`,
    next_actions:
      status === "completed"
        ? ["read receipt.artifact_refs"]
        : [`inspect ${status} receipt before retrying`],
    artifacts: [],
    receipt: {
      schema_version: 1,
      operation_id: input.operationId,
      attempt_id: input.attemptId,
      status,
      artifact_refs: [],
    },
    stderr: "",
  };
}

async function requests() {
  const controllerCwd = await controller();
  return {
    execute: invocation(controllerCwd, IDS[0]),
    judge: invocation(controllerCwd, IDS[1]),
    collect: invocation(controllerCwd, IDS[2]),
    improve: invocation(controllerCwd, IDS[3]),
    compare: invocation(controllerCwd, IDS[4]),
    select: invocation(controllerCwd, IDS[5]),
  };
}

test("composes controller-owned requests in the required order", async () => {
  const calls: string[] = [];
  const result = await runWorkflow(await requests(), async (input) => {
    calls.push(input.operationId);
    return receipt(input, "completed");
  });
  assert.deepEqual(calls, IDS);
  assert.equal(result.select.operation_id, IDS[5]);
  assert.equal(result.stages.length, 6);
  assert.equal(result.stages[5]?.status, "completed");
});

test("does not schedule subsequent stages after a failed receipt", async () => {
  const calls: string[] = [];
  await assert.rejects(
    runWorkflow(await requests(), async (input) => {
      calls.push(input.operationId);
      return receipt(input, calls.length === 2 ? "failed" : "completed");
    }),
    (error: unknown) =>
      error instanceof WorkflowError &&
      error.stage === "judge" &&
      error.status === "failed" &&
      error.next_actions.some((action) => action.includes("failed")),
  );
  assert.deepEqual(calls, IDS.slice(0, 2));
});

test("propagates cancelled receipts without consuming later requests", async () => {
  const calls: string[] = [];
  await assert.rejects(
    runWorkflow(await requests(), async (input) => {
      calls.push(input.operationId);
      return receipt(input, calls.length === 1 ? "cancelled" : "completed");
    }),
    /execute.*cancelled/,
  );
  assert.deepEqual(calls, IDS.slice(0, 1));
});

test("blocks before dispatch when controller schema artifacts are absent", async () => {
  const input = await requests();
  await writeFile(
    join(input.execute.controllerCwd, ".syndicate", "schemas", "command-request-v1.json"),
    "not-json",
  );
  await assert.rejects(runWorkflow(input), /schema/);
});
