import assert from "node:assert/strict";
import test from "node:test";

import boundsExtension, { DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS, bashSentence, boundTimeout, registerBounds } from "../packages/engine/bounds.ts";

const CONTEXT = JSON.stringify({ version: 1, repo: "/w", contract: "/w/c.yaml", revisions: {},
	writable_paths: ["src/*"], test_command: ["uv", "run", "python", "-m", "pytest", "-q"], symbols: {},
	carried: [], base_commit: "b".repeat(40) });
const CMD = ["uv", "run", "python", "-m", "pytest", "-q"];

function fakePi() {
	const handlers = {}; const entries = [];
	return { pi: { on(event, h) { (handlers[event] ??= []).push(h); }, registerTool() {}, async appendEntry(kind, data) { entries.push({ kind, data }); } }, handlers, entries };
}

test("the frozen values are 120 and 300", () => {
	assert.equal(DEFAULT_TIMEOUT_SECONDS, 120);
	assert.equal(MAX_TIMEOUT_SECONDS, 300);
});

test("boundTimeout sets an absent or bad value, clamps a large one, keeps a sane one", () => {
	assert.deepEqual(boundTimeout(undefined), { timeout: 120, action: "set" });
	assert.deepEqual(boundTimeout(0), { timeout: 120, action: "set" });
	assert.deepEqual(boundTimeout(-5), { timeout: 120, action: "set" });
	assert.deepEqual(boundTimeout("60"), { timeout: 120, action: "set" });   // cannot occur live: Pi coerces numeric strings first
	assert.deepEqual(boundTimeout(Number.POSITIVE_INFINITY), { timeout: 120, action: "set" });
	assert.deepEqual(boundTimeout(900), { timeout: 300, action: "clamped" });
	assert.deepEqual(boundTimeout(300), { timeout: 300, action: "kept" });
	assert.deepEqual(boundTimeout(60), { timeout: 60, action: "kept" });
});

test("a bash call without a timeout has one after the handler, clamps record an entry, kept records none, and nothing is blocked", async () => {
	const { pi, handlers, entries } = fakePi();
	registerBounds(pi, CMD);
	const absent = { toolCallId: "1", toolName: "bash", input: { command: "find / -name x" } };
	assert.equal(await handlers.tool_call[0](absent), undefined);
	assert.equal(absent.input.timeout, 120);
	const big = { toolCallId: "2", toolName: "bash", input: { command: "sleep 1", timeout: 900 } };
	await handlers.tool_call[0](big);
	assert.equal(big.input.timeout, 300);
	const sane = { toolCallId: "3", toolName: "bash", input: { command: "sleep 1", timeout: 45 } };
	await handlers.tool_call[0](sane);
	assert.equal(sane.input.timeout, 45);
	assert.deepEqual(entries, [
		{ kind: "command_bounded", data: { toolCallId: "1", action: "set", timeout: 120 } },
		{ kind: "command_bounded", data: { toolCallId: "2", action: "clamped", timeout: 300 } },
	]);
});

test("other tools are untouched", async () => {
	const { pi, handlers } = fakePi();
	registerBounds(pi, []);
	const event = { toolCallId: "1", toolName: "read", input: { path: "a" } };
	assert.equal(await handlers.tool_call[0](event), undefined);
	assert.deepEqual(event.input, { path: "a" });
});

test("the bash result gains exactly one sentence naming the bound and the self-test; a timeout is recorded", async () => {
	const { pi, handlers, entries } = fakePi();
	registerBounds(pi, CMD);
	await handlers.tool_call[0]({ toolCallId: "1", toolName: "bash", input: { command: "ls" } });
	const patch = await handlers.tool_result[0]({ toolCallId: "1", toolName: "bash", input: { command: "ls", timeout: 120 }, isError: false,
		content: [{ type: "text", text: "a\nb\n" }], details: {} });
	assert.deepEqual(patch, { content: [{ type: "text", text: "a\nb\n\n" + bashSentence(120, CMD) }] });
	assert.equal(bashSentence(120, CMD),
		'Commands here are bounded at 120 seconds; on timeout Pi kills the process group. The self-test is "uv run python -m pytest -q"; run it with the self_test tool.');
	assert.equal(bashSentence(300, []), "Commands here are bounded at 300 seconds; on timeout Pi kills the process group.");
	await handlers.tool_call[0]({ toolCallId: "2", toolName: "bash", input: { command: "find /" } });
	await handlers.tool_result[0]({ toolCallId: "2", toolName: "bash", input: { command: "find /", timeout: 120 }, isError: true,
		content: [{ type: "text", text: "partial\n\nCommand timed out after 120 seconds" }], details: {} });
	assert.deepEqual(entries.at(-1), { kind: "command_timed_out", data: { toolCallId: "2", timeout: 120 } });
	assert.equal(await handlers.tool_result[0]({ toolCallId: "9", toolName: "read", content: [], details: {} }), undefined);
});

test("the timeout phrase in a successful result's own text is not recorded as a firing", async () => {
	// Final review fix: `echo`/`grep` output can contain the exact phrase
	// without the command ever having timed out. Only `isError: true` plus
	// the phrase counts; the bound sentence is still appended either way.
	const { pi, handlers, entries } = fakePi();
	registerBounds(pi, CMD);
	await handlers.tool_call[0]({ toolCallId: "3", toolName: "bash", input: { command: "echo 'Command timed out after 120 seconds'" } });
	const patch = await handlers.tool_result[0]({
		toolCallId: "3",
		toolName: "bash",
		input: { command: "echo 'Command timed out after 120 seconds'", timeout: 120 },
		isError: false,
		content: [{ type: "text", text: "Command timed out after 120 seconds" }],
		details: {},
	});
	assert.deepEqual(patch, {
		content: [{ type: "text", text: "Command timed out after 120 seconds\n" + bashSentence(120, CMD) }],
	});
	assert.equal(entries.some((entry) => entry.kind === "command_timed_out"), false);
});

test("the default extension registers nothing without a mutation context and both handlers with one", () => {
	const bare = fakePi();
	boundsExtension(bare.pi, {});
	assert.deepEqual(bare.handlers, {});
	const child = fakePi();
	boundsExtension(child.pi, { SATYRN_MUTATION_CONTEXT: CONTEXT });
	assert.equal(child.handlers.tool_call.length, 1);
	assert.equal(child.handlers.tool_result.length, 1);
});
