import assert from "node:assert/strict";
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

function invocation(operationId: string): PythonInvocation {
  return {
    pythonExecutable: "/pinned/python",
    controllerCwd: "/controller",
    requestPath: `/controller/.syndicate/runs/${operationId}/${operationId}/request.json`,
    operationId,
    attemptId: operationId,
    timeoutMs: 1_000,
    environment: {},
  };
}

function receipt(input: PythonInvocation, status: CommandReceipt["status"]): TransportResult {
  return {
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

function requests() {
  return {
    execute: invocation(IDS[0]),
    judge: invocation(IDS[1]),
    collect: invocation(IDS[2]),
    improve: invocation(IDS[3]),
    compare: invocation(IDS[4]),
    select: invocation(IDS[5]),
  };
}

test("composes controller-owned requests in the required order", async () => {
  const calls: string[] = [];
  const result = await runWorkflow(requests(), async (input) => {
    calls.push(input.operationId);
    return receipt(input, "completed");
  });
  assert.deepEqual(calls, IDS);
  assert.equal(result.select.operation_id, IDS[5]);
});

test("does not schedule subsequent stages after a failed receipt", async () => {
  const calls: string[] = [];
  await assert.rejects(
    runWorkflow(requests(), async (input) => {
      calls.push(input.operationId);
      return receipt(input, calls.length === 2 ? "failed" : "completed");
    }),
    WorkflowError,
  );
  assert.deepEqual(calls, IDS.slice(0, 2));
});

test("propagates cancelled receipts without consuming later requests", async () => {
  const calls: string[] = [];
  await assert.rejects(
    runWorkflow(requests(), async (input) => {
      calls.push(input.operationId);
      return receipt(input, calls.length === 1 ? "cancelled" : "completed");
    }),
    /execute.*cancelled/,
  );
  assert.deepEqual(calls, IDS.slice(0, 1));
});
