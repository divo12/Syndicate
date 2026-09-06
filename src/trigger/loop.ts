import { type TaskResult, type TrialPayload } from "./trial.js";

export type { TaskResult, TrialPayload } from "./trial.js";

export type StopReason =
  | "all_tasks_passed"
  | "max_iterations"
  | "no_improvement"
  | "cancelled";

export type LoopPayload = Readonly<{
  taskIds: readonly string[];
  maxIterations: number;
  patience: number;
}>;

export type TrialBatch = Readonly<{
  taskIds: readonly string[];
  generation: number;
}>;

export type IterationRecord = Readonly<{
  iteration: number;
  generation: number;
  score: number;
  accepted: boolean;
  results: readonly TaskResult[];
}>;

export type LoopProgress = Readonly<{
  status: "running" | "completed" | "cancelled";
  iteration: number;
  generation: number;
  score: number;
  best_score: number;
  stop_reason: StopReason | null;
}>;

export type LoopReceipt = Readonly<{
  task_ids: readonly string[];
  status: "completed" | "cancelled";
  stop_reason: StopReason;
  best_score: number;
  iterations: readonly IterationRecord[];
}>;

export type LoopPorts = Readonly<{
  runTasks: (batch: TrialBatch) => Promise<readonly TaskResult[]>;
  improve: (generation: number) => Promise<number>;
  onProgress?: (state: LoopProgress) => Promise<void> | void;
  isCancelled?: () => boolean;
}>;

export class LearningLoopError extends Error {}

function uniqueTaskIds(taskIds: readonly string[]): readonly string[] {
  if (taskIds.length === 0 || taskIds.some((taskId) => taskId.trim() === "")) {
    throw new LearningLoopError("taskIds must contain at least one non-empty id");
  }
  if (new Set(taskIds).size !== taskIds.length) {
    throw new LearningLoopError("taskIds must be unique");
  }
  return taskIds;
}

function bounded(payload: LoopPayload): LoopPayload {
  const taskIds = uniqueTaskIds(payload.taskIds);
  if (
    !Number.isSafeInteger(payload.maxIterations) ||
    payload.maxIterations < 1 ||
    payload.maxIterations > 50 ||
    !Number.isSafeInteger(payload.patience) ||
    payload.patience < 1 ||
    payload.patience > 20
  ) {
    throw new LearningLoopError(
      "maxIterations and patience must be within 1..50 and 1..20",
    );
  }
  return { ...payload, taskIds };
}

function scoreOf(results: readonly TaskResult[], taskIds: readonly string[]): number {
  if (results.length !== taskIds.length) {
    throw new LearningLoopError("runTasks must return one result per task id");
  }
  const seen = new Set<string>();
  let passed = 0;
  for (const result of results) {
    if (seen.has(result.taskId) || !taskIds.includes(result.taskId)) {
      throw new LearningLoopError("runTasks returned an unexpected task id");
    }
    seen.add(result.taskId);
    passed += result.reward;
  }
  return passed / taskIds.length;
}

function decideStop(
  score: number,
  stagnant: number,
  iteration: number,
  payload: LoopPayload,
): StopReason | null {
  if (score === 1) {
    return "all_tasks_passed";
  }
  if (stagnant >= payload.patience) {
    return "no_improvement";
  }
  if (iteration + 1 >= payload.maxIterations) {
    return "max_iterations";
  }
  return null;
}

export async function runLearningLoop(
  raw: LoopPayload,
  ports: LoopPorts,
): Promise<LoopReceipt> {
  const payload = bounded(raw);
  const iterations: IterationRecord[] = [];
  let generation = 0;
  let best = -1;
  let stagnant = 0;

  for (let iteration = 0; iteration < payload.maxIterations; iteration += 1) {
    if (ports.isCancelled?.()) {
      return finish(payload, iterations, best, "cancelled", ports, generation);
    }
    const results = await ports.runTasks({
      taskIds: payload.taskIds,
      generation,
    });
    if (ports.isCancelled?.()) {
      return finish(payload, iterations, best, "cancelled", ports, generation);
    }
    const score = scoreOf(results, payload.taskIds);
    const accepted = score > best;
    if (accepted) {
      best = score;
      stagnant = 0;
    } else {
      stagnant += 1;
    }
    iterations.push({ iteration, generation, score, accepted, results });
    const stop = decideStop(score, stagnant, iteration, payload);
    await ports.onProgress?.({
      status: stop === "cancelled" ? "cancelled" : stop === null ? "running" : "completed",
      iteration,
      generation,
      score,
      best_score: best,
      stop_reason: stop,
    });
    if (stop !== null) {
      return finish(payload, iterations, best, stop, ports, generation);
    }
    generation = await ports.improve(generation);
    if (ports.isCancelled?.()) {
      return finish(payload, iterations, best, "cancelled", ports, generation);
    }
  }
  return finish(payload, iterations, best, "max_iterations", ports, generation);
}

async function finish(
  payload: LoopPayload,
  iterations: readonly IterationRecord[],
  best: number,
  stop: StopReason,
  ports: LoopPorts,
  generation: number,
): Promise<LoopReceipt> {
  const status = stop === "cancelled" ? "cancelled" : "completed";
  const last = iterations[iterations.length - 1];
  await ports.onProgress?.({
    status,
    iteration: last?.iteration ?? -1,
    generation,
    score: last?.score ?? 0,
    best_score: Math.max(best, 0),
    stop_reason: stop,
  });
  return {
    task_ids: payload.taskIds,
    status,
    stop_reason: stop,
    best_score: Math.max(best, 0),
    iterations,
  };
}
