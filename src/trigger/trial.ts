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

function parseTrial(stdout: string): TaskResult {
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
    typeof value.reward !== "number"
  ) {
    throw new Error("Invalid trial receipt");
  }
  return { taskId: value.task_id, outcome: value.outcome, reward: value.reward };
}

export async function executeTrial(
  payload: TrialPayload,
  pythonExecutable: string | undefined = process.env.SYNDICATE_PYTHON,
): Promise<TaskResult> {
  if (pythonExecutable === undefined || pythonExecutable.length === 0) {
    return simulateTrial(payload);
  }
  const stdout = await new Promise<string>((resolve, reject) => {
    const child = spawn(pythonExecutable, [
      "-m",
      "syndicate.cli",
      "trial",
      "--task-id",
      payload.taskId,
      "--generation",
      String(payload.generation),
    ]);
    let output = "";
    let stderr = "";
    child.stdout.on("data", (chunk: Buffer) => {
      output += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.once("error", (error: Error) => {
      reject(error);
    });
    child.once("close", (code: number | null) => {
      if (code === 0) {
        resolve(output);
        return;
      }
      reject(new Error(`trial exited ${String(code)}: ${stderr}`));
    });
  });
  return parseTrial(stdout);
}
