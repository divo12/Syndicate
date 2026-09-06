import assert from "node:assert/strict";
import test from "node:test";

import {
  LearningLoopError,
  runLearningLoop,
  type LoopPayload,
  type LoopPorts,
  type TaskResult,
  type TrialBatch,
} from "../src/trigger/loop.js";
import { executeTrial, simulateTrial } from "../src/trigger/trial.js";
import { learningLoop, runTrial } from "../src/trigger/learning.js";

const TASKS = ["regex-log", "extract-elf", "log-summary-date-ranges"] as const;

function payload(overrides: Partial<LoopPayload> = {}): LoopPayload {
  return {
    taskIds: TASKS,
    maxIterations: 3,
    patience: 2,
    ...overrides,
  };
}

function result(taskId: string, passed: boolean): TaskResult {
  return {
    taskId,
    outcome: passed ? "passed" : "failed",
    reward: passed ? 1 : 0,
  };
}

function simulatedPorts(
  generations: number[] = [],
): LoopPorts & { batches: TrialBatch[] } {
  const batches: TrialBatch[] = [];
  return {
    batches,
    async runTasks(batch) {
      batches.push(batch);
      return batch.taskIds.map((taskId) =>
        simulateTrial({
          taskId,
          generation: batch.generation,
          failingTaskId: batch.taskIds[batch.taskIds.length - 1] ?? taskId,
        }),
      );
    },
    async improve(generation) {
      generations.push(generation);
      return generation + 1;
    },
  };
}

test("learning-loop and run-trial are registered Trigger tasks", () => {
  assert.equal(learningLoop.id, "learning-loop");
  assert.equal(runTrial.id, "run-trial");
});

test("rejects empty or duplicate task ids before any trial runs", async () => {
  const ports = simulatedPorts();
  await assert.rejects(
    runLearningLoop(payload({ taskIds: [] }), ports),
    (error: unknown) =>
      error instanceof LearningLoopError && error.message.includes("taskIds"),
  );
  await assert.rejects(
    runLearningLoop(payload({ taskIds: ["a", "a"] }), ports),
    /unique/,
  );
  assert.equal(ports.batches.length, 0);
});

test("fans one or many tasks through one batch per iteration", async () => {
  const many = simulatedPorts();
  const manyReceipt = await runLearningLoop(payload(), many);
  assert.equal(manyReceipt.stop_reason, "all_tasks_passed");
  assert.equal(manyReceipt.best_score, 1);
  assert.equal(manyReceipt.iterations.length, 2);
  assert.deepEqual(
    many.batches.map((batch) => [...batch.taskIds]),
    [TASKS, TASKS],
  );
  assert.deepEqual(
    many.batches.map((batch) => batch.generation),
    [0, 1],
  );

  const one = simulatedPorts();
  const oneReceipt = await runLearningLoop(
    payload({ taskIds: ["regex-log"] }),
    one,
  );
  assert.equal(oneReceipt.stop_reason, "all_tasks_passed");
  assert.equal(one.batches[0]?.taskIds.length, 1);
  assert.equal(oneReceipt.iterations[0]?.score, 0);
  assert.equal(oneReceipt.iterations[1]?.score, 1);
});

test("accepts a candidate only when the score strictly improves", async () => {
  const receipt = await runLearningLoop(payload(), simulatedPorts());
  assert.equal(receipt.iterations[0]?.accepted, true);
  assert.equal(receipt.iterations[0]?.score, 2 / 3);
  assert.equal(receipt.iterations[1]?.accepted, true);
  assert.equal(receipt.iterations[1]?.score, 1);
});

test("stops after patience non-improving iterations", async () => {
  const ports: LoopPorts = {
    async runTasks(batch) {
      return batch.taskIds.map((taskId, index) =>
        result(taskId, index !== batch.taskIds.length - 1),
      );
    },
    async improve(generation) {
      return generation + 1;
    },
  };
  const receipt = await runLearningLoop(
    payload({ maxIterations: 5, patience: 2 }),
    ports,
  );
  assert.equal(receipt.stop_reason, "no_improvement");
  assert.equal(receipt.iterations.length, 3);
  assert.equal(receipt.best_score, 2 / 3);
});

test("stops at the iteration limit when tasks never all pass", async () => {
  const ports: LoopPorts = {
    async runTasks(batch) {
      return batch.taskIds.map((taskId) => result(taskId, false));
    },
    async improve(generation) {
      return generation;
    },
  };
  const receipt = await runLearningLoop(
    payload({ maxIterations: 2, patience: 9 }),
    ports,
  );
  assert.equal(receipt.stop_reason, "max_iterations");
  assert.equal(receipt.iterations.length, 2);
});

test("does not start another iteration after cancel", async () => {
  const batches: TrialBatch[] = [];
  let calls = 0;
  const ports: LoopPorts = {
    async runTasks(batch) {
      batches.push(batch);
      return batch.taskIds.map((taskId) => result(taskId, false));
    },
    async improve(generation) {
      return generation + 1;
    },
    isCancelled() {
      calls += 1;
      return calls > 1;
    },
  };
  const receipt = await runLearningLoop(payload(), ports);
  assert.equal(receipt.stop_reason, "cancelled");
  assert.equal(batches.length, 1);
});

test("executeTrial uses the in-process simulator without SYNDICATE_PYTHON", async () => {
  const result = await executeTrial({
    taskId: "regex-log",
    generation: 0,
    failingTaskId: "regex-log",
  });
  assert.equal(result.outcome, "failed");
});
