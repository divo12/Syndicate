import { readFile } from "node:fs/promises";
import { join } from "node:path";

import {
  type CommandReceipt,
  type PythonInvocation,
  type TransportResult,
  invokePython,
} from "./transport.js";

export type WorkflowRequests = Readonly<{
  execute: PythonInvocation;
  judge: PythonInvocation;
  collect: PythonInvocation;
  improve: PythonInvocation;
  compare: PythonInvocation;
  select: PythonInvocation;
}>;

export type StageStatus = Readonly<{
  stage: keyof WorkflowRequests;
  status: TransportResult["status"] | CommandReceipt["status"];
  summary: string;
  next_actions: readonly string[];
  artifacts: readonly string[];
  receipt: CommandReceipt;
}>;

export type WorkflowReceipt = Readonly<{
  execute: CommandReceipt;
  judge: CommandReceipt;
  collect: CommandReceipt;
  improve: CommandReceipt;
  compare: CommandReceipt;
  select: CommandReceipt;
  stages: readonly StageStatus[];
}>;

type Invoke = (input: PythonInvocation) => Promise<TransportResult>;
type Stage = keyof WorkflowRequests;

export class WorkflowError extends Error {
  readonly stage: Stage;
  readonly status: string;
  readonly next_actions: readonly string[];

  constructor(stage: Stage, status: string, next_actions: readonly string[]) {
    super(
      `${stage} returned ${status}; next_actions: ${next_actions.join(" | ")}`,
    );
    this.stage = stage;
    this.status = status;
    this.next_actions = next_actions;
  }
}

async function requireControllerSchemas(requests: WorkflowRequests): Promise<void> {
  const controllerCwd = requests.execute.controllerCwd;
  if (
    [
      requests.judge,
      requests.collect,
      requests.improve,
      requests.compare,
      requests.select,
    ].some((request) => request.controllerCwd !== controllerCwd)
  ) {
    throw new WorkflowError("execute", "blocked", [
      "stop: workflow requests must share one controller cwd",
    ]);
  }
  try {
    const schemas = await Promise.all(
      ["command-request-v1.json", "command-receipt-v1.json"].map((name) =>
        readFile(join(controllerCwd, ".syndicate", "schemas", name), "utf8"),
      ),
    );
    schemas.forEach((schema) => JSON.parse(schema));
  } catch {
    throw new WorkflowError("execute", "blocked", [
      "stop: export controller schema artifacts before dispatch",
    ]);
  }
}

function stageActions(stage: Stage, result: TransportResult): readonly string[] {
  if (result.next_actions.length > 0) {
    return result.next_actions;
  }
  return [`inspect ${stage} receipt.status and stderr before continuing`];
}

async function runStage(
  stage: Stage,
  input: PythonInvocation,
  invoke: Invoke,
): Promise<StageStatus> {
  const result = await invoke(input);
  const next_actions = stageActions(stage, result);
  if (result.status === "error" || result.receipt === undefined) {
    throw new WorkflowError(stage, result.status, next_actions);
  }
  if (result.receipt.status !== "completed") {
    throw new WorkflowError(stage, result.receipt.status, next_actions);
  }
  return {
    stage,
    status: result.receipt.status,
    summary: result.summary,
    next_actions,
    artifacts: result.artifacts,
    receipt: result.receipt,
  };
}

export async function runWorkflow(
  requests: WorkflowRequests,
  invoke: Invoke = invokePython,
): Promise<WorkflowReceipt> {
  await requireControllerSchemas(requests);
  const execute = await runStage("execute", requests.execute, invoke);
  const judge = await runStage("judge", requests.judge, invoke);
  const collect = await runStage("collect", requests.collect, invoke);
  const improve = await runStage("improve", requests.improve, invoke);
  const compare = await runStage("compare", requests.compare, invoke);
  const select = await runStage("select", requests.select, invoke);
  return {
    execute: execute.receipt,
    judge: judge.receipt,
    collect: collect.receipt,
    improve: improve.receipt,
    compare: compare.receipt,
    select: select.receipt,
    stages: [execute, judge, collect, improve, compare, select],
  };
}
