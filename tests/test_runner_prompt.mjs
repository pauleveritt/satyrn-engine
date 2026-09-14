import assert from "node:assert/strict";
import test from "node:test";

import { registerRunner } from "../packages/engine/runner.ts";

// pi lists a tool under "Available tools" only when its registration supplies a
// promptSnippet. `self_test` no longer shadows native `bash` (Ruling 1, Phase 1
// Task 4) -- it must still be named in --tools or the registered tool does not
// exist -- and it restores the carried set, then runs only the contract's
// declared self-test command. This line is the only place the model is told
// so before it tries.

const CONTEXT = { repo: "/repo", contract: { test_command: ["uv", "run", "pytest"] }, revisions: {} };

function registered() {
	const tools = [];
	registerRunner({ registerTool: (tool) => tools.push(tool), on: () => undefined },
		CONTEXT, async () => ({ ok: true, result: null }));
	return tools;
}

test("the bounded runner registers a prompt snippet naming its one command", () => {
	const selfTest = registered().find((tool) => tool.name === "self_test");
	assert.ok(selfTest, "the runner registers a self_test tool");
	assert.equal(typeof selfTest.promptSnippet, "string");
	assert.match(selfTest.promptSnippet, /declared self-test command/i);
});

test("the snippet does not repeat the shell exploration pi suggests", () => {
	// The refusal direction: a snippet mentioning ls/rg/find would reinforce
	// the guideline it exists to counteract.
	const selfTest = registered().find((tool) => tool.name === "self_test");
	assert.doesNotMatch(selfTest.promptSnippet, /\bls\b|\brg\b|\bfind\b/);
});
