import { realpath } from "node:fs/promises";
import { spawn } from "node:child_process";

const GRACE_MS = 5_000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type JsonObject = Readonly<Record<string, unknown>>;

export type CommandStatus = "completed" | "failed" | "blocked" | "cancelled";

export type ArtifactRef = Readonly<{
  kind: string;
  operation_id: string;
  attempt_id: string;
  sha256: string;
}>;

export type CommandReceipt = Readonly<{
  schema_version: 1;
  operation_id: string | null;
  attempt_id: string | null;
  status: CommandStatus;
  artifact_refs: readonly ArtifactRef[];
}>;

export type PythonInvocation = Readonly<{
  pythonExecutable: string;
  controllerCwd: string;
  requestPath: string;
  operationId: string;
  attemptId: string;
  timeoutMs: number;
  environment: Readonly<Record<string, string>>;
  signal?: AbortSignal;
}>;

export type TransportResult = Readonly<{
  receipt: CommandReceipt;
  stderr: string;
}>;

export class TransportError extends Error {}

function record(value: unknown): JsonObject | undefined {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as JsonObject)
    : undefined;
}

function receiptFrom(stdout: string, input: PythonInvocation): CommandReceipt {
  const lines = stdout.split(/\r?\n/).filter((line) => line.length > 0);
  if (lines.length !== 1 || lines[0] === undefined) {
    throw new TransportError("Expected exactly one stdout receipt");
  }
  let value: unknown;
  try {
    value = JSON.parse(lines[0]);
  } catch {
    throw new TransportError("Stdout receipt is not JSON");
  }
  const raw = record(value);
  const status = raw?.status;
  const artifactRefs = raw?.artifact_refs;
  if (
    raw?.schema_version !== 1 ||
    raw.operation_id !== input.operationId ||
    raw.attempt_id !== input.attemptId ||
    !isStatus(status) ||
    !Array.isArray(artifactRefs) ||
    !artifactRefs.every(isArtifactRef)
  ) {
    throw new TransportError("Invalid or mismatched stdout receipt");
  }
  return {
    schema_version: 1,
    operation_id: input.operationId,
    attempt_id: input.attemptId,
    status,
    artifact_refs: artifactRefs,
  };
}

function isStatus(value: unknown): value is CommandStatus {
  return (
    value === "completed" ||
    value === "failed" ||
    value === "blocked" ||
    value === "cancelled"
  );
}

function isArtifactRef(value: unknown): value is ArtifactRef {
  const raw = record(value);
  return (
    typeof raw?.kind === "string" &&
    typeof raw.operation_id === "string" &&
    typeof raw.attempt_id === "string" &&
    typeof raw.sha256 === "string"
  );
}

async function validatePaths(input: PythonInvocation): Promise<void> {
  if (!UUID.test(input.operationId) || !UUID.test(input.attemptId)) {
    throw new TransportError("Operation and attempt IDs must be UUIDs");
  }
  const cwd = await realpath(input.controllerCwd);
  const request = await realpath(input.requestPath);
  const expected = `${cwd}/.syndicate/runs/${input.operationId}/${input.attemptId}/request.json`;
  if (request !== expected) {
    throw new TransportError("Request is not controller-owned");
  }
}

function stop(pid: number, signal: NodeJS.Signals): void {
  try {
    process.kill(-pid, signal);
  } catch {
    try {
      process.kill(pid, signal);
    } catch {
      // The owned process has already exited.
    }
  }
}

async function execute(input: PythonInvocation): Promise<{
  stdout: string;
  stderr: string;
  code: number | null;
  stopped: boolean;
}> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      input.pythonExecutable,
      ["-m", "syndicate.cli", "execute", "--request", input.requestPath],
      { cwd: input.controllerCwd, detached: true, env: input.environment },
    );
    let stdout = "";
    let stderr = "";
    let stopped = false;
    const end = (signal: NodeJS.Signals): void => {
      stopped = true;
      if (child.pid !== undefined) stop(child.pid, signal);
    };
    const deadline = setTimeout(() => end("SIGTERM"), input.timeoutMs);
    const force = setTimeout(() => end("SIGKILL"), input.timeoutMs + GRACE_MS);
    const abort = (): void => end("SIGTERM");
    if (input.signal?.aborted) {
      abort();
    } else {
      input.signal?.addEventListener("abort", abort, { once: true });
    }
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk.toString();
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk.toString();
    });
    child.once("error", (error: Error) => {
      clearTimeout(deadline);
      clearTimeout(force);
      input.signal?.removeEventListener("abort", abort);
      reject(new TransportError(`Python launch failed: ${error.message}`));
    });
    child.once("close", (code: number | null) => {
      clearTimeout(deadline);
      clearTimeout(force);
      input.signal?.removeEventListener("abort", abort);
      resolve({ stdout, stderr, code, stopped });
    });
  });
}

export async function invokePython(input: PythonInvocation): Promise<TransportResult> {
  if (!Number.isSafeInteger(input.timeoutMs) || input.timeoutMs <= 0) {
    throw new TransportError("Timeout must be a positive integer");
  }
  await validatePaths(input);
  const result = await execute(input);
  if (result.stopped) {
    throw new TransportError("Python command stopped at cancellation or deadline");
  }
  if (result.code !== 0) {
    throw new TransportError(`Python command exited ${String(result.code)}`);
  }
  return { receipt: receiptFrom(result.stdout, input), stderr: result.stderr };
}
