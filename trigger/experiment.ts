import { task } from "@trigger.dev/sdk";

import { runWorkflow, type WorkflowReceipt, type WorkflowRequests } from "./workflow.js";

export const controllerWorkflow = task({
  id: "controller-workflow",
  maxDuration: 300,
  retry: { maxAttempts: 1 },
  run: async (requests: WorkflowRequests): Promise<WorkflowReceipt> =>
    runWorkflow(requests),
});
