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

export type WorkflowReceipt = Readonly<{
  execute: CommandReceipt;
  judge: CommandReceipt;
  collect: CommandReceipt;
  improve: CommandReceipt;
  compare: CommandReceipt;
  select: CommandReceipt;
}>;

type Invoke = (input: PythonInvocation) => Promise<TransportResult>;
type Stage = keyof WorkflowRequests;

export class WorkflowError extends Error {}

async function runStage(
  stage: Stage,
  input: PythonInvocation,
  invoke: Invoke,
): Promise<CommandReceipt> {
  const receipt = (await invoke(input)).receipt;
  if (receipt.status !== "completed") {
    throw new WorkflowError(`${stage} returned ${receipt.status}`);
  }
  return receipt;
}

export async function runWorkflow(
  requests: WorkflowRequests,
  invoke: Invoke = invokePython,
): Promise<WorkflowReceipt> {
  const execute = await runStage("execute", requests.execute, invoke);
  const judge = await runStage("judge", requests.judge, invoke);
  const collect = await runStage("collect", requests.collect, invoke);
  const improve = await runStage("improve", requests.improve, invoke);
  const compare = await runStage("compare", requests.compare, invoke);
  const select = await runStage("select", requests.select, invoke);
  return { execute, judge, collect, improve, compare, select };
}
