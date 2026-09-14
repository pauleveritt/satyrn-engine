import assert from "node:assert/strict";
import test from "node:test";

import { registerRunner } from "../packages/engine/runner.ts";

// pi lists a tool under "Available tools" only when its registration supplies a
// promptSnippet, and its default guidelines tell the model to "use bash for
// file operations like ls, rg, find" whenever bash is selected and no
// grep/find/ls tool is (pi's system-prompt builder). The engine's `bash` IS
// selected -- it must be named in --tools or the registered tool does not exist
// -- and it refuses every command but the contract's declared test command.
// This line is the only place the model is told so before it tries.

const CONTEXT = { repo: "/repo", contract: { test_command: ["uv", "run", "pytest"] }, revisions: {} };

function registered() {
	const tools = [];
	registerRunner({ registerTool: (tool) => tools.push(tool), on: () => undefined },
		CONTEXT, async () => ({ ok: true, result: null }));
	return tools;
}

test("the bounded runner registers a prompt snippet naming its one command", () => {
	const bash = registered().find((tool) => tool.name === "bash");
	assert.ok(bash, "the runner registers a bash tool");
	assert.equal(typeof bash.promptSnippet, "string");
	assert.match(bash.promptSnippet, /only|exact/i);
});

test("the snippet does not repeat the shell exploration pi suggests", () => {
	// The refusal direction: a snippet mentioning ls/rg/find would reinforce
	// the guideline it exists to counteract.
	const bash = registered().find((tool) => tool.name === "bash");
	assert.doesNotMatch(bash.promptSnippet, /\bls\b|\brg\b|\bfind\b/);
});
