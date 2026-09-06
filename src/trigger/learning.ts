import { metadata, task } from "@trigger.dev/sdk";

import {
  runLearningLoop,
  type LoopPayload,
  type LoopPorts,
  type LoopReceipt,
} from "./loop.js";
import { executeTrial, type TaskResult, type TrialPayload } from "./trial.js";

type RunResult<T> = { ok: true; output: T } | { ok: false };

export const runTrial = task({
  id: "run-trial",
  retry: { maxAttempts: 1 },
  run: async (payload: TrialPayload): Promise<TaskResult> => executeTrial(payload),
});

export const improveHarness = task({
  id: "improve-harness",
  retry: { maxAttempts: 1 },
  run: async (payload: {
    generation: number;
  }): Promise<{ generation: number }> => ({ generation: payload.generation + 1 }),
});

function requireOutput<T>(result: RunResult<T>, stage: string): T {
  if (!result.ok) {
    throw new Error(`${stage} child run failed`);
  }
  return result.output;
}

export function triggerLoopPorts(): LoopPorts {
  return {
    async runTasks(batch) {
      const failingTaskId = batch.taskIds[batch.taskIds.length - 1];
      if (failingTaskId === undefined) {
        return [];
      }
      const batchResult = await runTrial.batchTriggerAndWait(
        batch.taskIds.map((taskId) => ({
          payload: {
            taskId,
            generation: batch.generation,
            failingTaskId,
          },
        })),
      );
      return batchResult.runs.map((run) => requireOutput(run, "run-trial"));
    },
    async improve(generation) {
      const result = await improveHarness.triggerAndWait({ generation });
      return requireOutput(result, "improve-harness").generation;
    },
    async onProgress(state) {
      metadata.replace({ ...state });
    },
  };
}

export const learningLoop = task({
  id: "learning-loop",
  maxDuration: 3600,
  retry: { maxAttempts: 1 },
  run: async (payload: LoopPayload, { signal }): Promise<LoopReceipt> =>
    runLearningLoop(payload, {
      ...triggerLoopPorts(),
      isCancelled: () => signal.aborted,
    }),
});
