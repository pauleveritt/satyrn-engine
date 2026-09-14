import assert from "node:assert/strict";
import test from "node:test";

import { runToolCallHandlers, runToolResultHandlers } from "../tools/replay_events.mjs";

// R10: a regression test proving tools/replay_events.mjs chains handlers the
// way Pi does -- every handler runs in order, a `tool_call` block short-
// circuits later handlers, and a `tool_result` patch merges every handler's
// contribution rather than stopping at the first one that returns something.

test("runToolCallHandlers: a later block wins over an earlier non-block result", async () => {
	const decision = await runToolCallHandlers(
		[() => ({ block: false }), () => ({ block: true, reason: "second" })],
		{ toolCallId: "1", toolName: "write", input: {} },
	);
	assert.deepEqual(decision, { block: true, reason: "second" });
});

test("runToolCallHandlers: a later handler returning undefined does not erase an earlier non-block decision", async () => {
	const decision = await runToolCallHandlers(
		[() => ({ block: false }), () => undefined],
		{ toolCallId: "1", toolName: "write", input: {} },
	);
	assert.deepEqual(decision, { block: false });
});

test("runToolResultHandlers: every handler runs and sees the same event; a later isError merges onto the first handler's content", async () => {
	let seen;
	const patch = await runToolResultHandlers(
		[
			() => ({ content: [{ type: "text", text: "a" }] }),
			(event) => {
				seen = event.content;
				return { isError: true };
			},
		],
		{ toolName: "edit", content: [], details: undefined },
	);
	assert.deepEqual(patch.content, [{ type: "text", text: "a" }]);
	assert.equal(patch.isError, true);
	assert.deepEqual(seen, [{ type: "text", text: "a" }]);
});

test("runToolResultHandlers: a second handler's patch is returned even when the first handler returns nothing", async () => {
	const patch = await runToolResultHandlers(
		[() => undefined, () => ({ details: { x: 1 } })],
		{ toolName: "edit", content: [], details: undefined },
	);
	assert.notEqual(patch, undefined);
	assert.deepEqual(patch.details, { x: 1 });
});
