import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";

import { controllerWorkflow } from "../src/trigger/experiment.js";
import { improveHarness, learningLoop, runTrial } from "../src/trigger/learning.js";
import triggerConfig from "../trigger.config.js";

const ROOT = process.cwd();

async function readRoot(relative: string): Promise<string> {
  return readFile(join(ROOT, relative), "utf8");
}

test("trigger config pins the live project and src/trigger tasks", () => {
  assert.equal(triggerConfig.project, "proj_srwrnuundwqhsddslwzg");
  assert.deepEqual(triggerConfig.dirs, ["./src/trigger"]);
  assert.equal(typeof triggerConfig.maxDuration, "number");
  assert.ok(triggerConfig.maxDuration > 0);
});

test("controller workflow task is exported from src/trigger via the SDK task helper", async () => {
  const source = await readRoot("src/trigger/experiment.ts");
  assert.match(source, /from "@trigger\.dev\/sdk"/);
  assert.doesNotMatch(source, /@trigger\.dev\/sdk\/v3/);
  assert.doesNotMatch(source, /defineJob/);
  assert.doesNotMatch(source, /node-fetch/);
  assert.equal(controllerWorkflow.id, "controller-workflow");
  assert.equal(learningLoop.id, "learning-loop");
  assert.equal(runTrial.id, "run-trial");
  assert.equal(improveHarness.id, "improve-harness");
});

test("tsconfig and gitignore cover the official Trigger layout", async () => {
  const tsconfig = JSON.parse(await readRoot("tsconfig.json")) as {
    include: readonly string[];
  };
  assert.ok(tsconfig.include.includes("trigger.config.ts"));
  assert.ok(tsconfig.include.includes("src/trigger/**/*.ts"));
  const gitignore = await readRoot(".gitignore");
  assert.match(gitignore, /^\.trigger$/m);
});

test("package pins the latest SDK and build packages without a v3 import", async () => {
  const pkg = JSON.parse(await readRoot("package.json")) as {
    dependencies: Readonly<Record<string, string>>;
    devDependencies: Readonly<Record<string, string>>;
    packageManager: string;
  };
  assert.match(pkg.packageManager, /^bun@/);
  assert.equal(pkg.dependencies["@trigger.dev/sdk"], "4.5.16");
  assert.equal(pkg.devDependencies["@trigger.dev/build"], "4.5.16");
  const files = [
    "src/trigger/experiment.ts",
    "src/trigger/learning.ts",
    "src/trigger/loop.ts",
    "src/trigger/transport.ts",
    "src/trigger/trial.ts",
    "src/trigger/workflow.ts",
    "trigger.config.ts",
  ];
  for (const file of files) {
    const source = await readRoot(file);
    assert.doesNotMatch(source, /@trigger\.dev\/sdk\/v3/);
    assert.doesNotMatch(source, /node-fetch/);
    assert.doesNotMatch(source, /defineJob/);
    assert.doesNotMatch(source, /Promise\.all\([\s\S]*(?:triggerAndWait|batchTriggerAndWait|\.wait\()/);
  }
});
