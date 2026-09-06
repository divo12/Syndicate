import { spawn } from "node:child_process";

export type TaskOutcome = "passed" | "failed" | "infra_error";

export type TrialPayload = Readonly<{
  taskId: string;
  generation: number;
  failingTaskId: string;
}>;

export type TaskResult = Readonly<{
  taskId: string;
  outcome: TaskOutcome;
  reward: number;
}>;

export function simulateTrial(payload: TrialPayload): TaskResult {
  const passed =
    payload.generation > 0 || payload.taskId !== payload.failingTaskId;
  return {
    taskId: payload.taskId,
    outcome: passed ? "passed" : "failed",
    reward: passed ? 1 : 0,
  };
}

export function parseTrial(stdout: string): TaskResult {
  const line = stdout.split(/\r?\n/).find((item) => item.length > 0);
  if (line === undefined) {
    throw new Error("Expected one trial receipt");
  }
  const value = JSON.parse(line) as {
    task_id?: unknown;
    outcome?: unknown;
    reward?: unknown;
  };
  if (
    typeof value.task_id !== "string" ||
    (value.outcome !== "passed" &&
      value.outcome !== "failed" &&
      value.outcome !== "infra_error") ||
    typeof value.reward !== "number" ||
    !Number.isFinite(value.reward) ||
    value.reward < 0 ||
    value.reward > 1
  ) {
    throw new Error("Invalid trial receipt");
  }
  return { taskId: value.task_id, outcome: value.outcome, reward: value.reward };
}

export function runProcess(
  file: string,
  args: readonly string[],
  timeoutMs: number,
): Promise<string> {
  return new Promise((resolve, reject) => {
    const child = spawn(file, [...args], { stdio: ["ignore", "pipe", "pipe"] });
    let output = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill("SIGTERM");
      reject(new Error(`trial timed out after ${String(timeoutMs)}ms`));
    }, timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.once("error", (error: Error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("close", (code: number | null) => {
      clearTimeout(timer);
      if (code === 0) {
        resolve(output);
        return;
      }
      reject(new Error(`trial exited ${String(code)}: ${stderr}`));
    });
  });
}

export async function executeTrial(
  payload: TrialPayload,
  pythonExecutable: string | undefined = process.env.SYNDICATE_PYTHON,
  timeoutMs: number = Number(process.env.SYNDICATE_TRIAL_TIMEOUT_MS ?? 60_000),
): Promise<TaskResult> {
  if (pythonExecutable === undefined || pythonExecutable.length === 0) {
    return simulateTrial(payload);
  }
  const stdout = await runProcess(
    pythonExecutable,
    [
      "-m",
      "syndicate.cli",
      "trial",
      "--task-id",
      payload.taskId,
      "--generation",
      String(payload.generation),
      "--failing-task-id",
      payload.failingTaskId,
    ],
    timeoutMs,
  );
  return parseTrial(stdout);
}
