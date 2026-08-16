#!/usr/bin/env node

/**
 * Replay recorded tool calls through the shipped TypeScript engine bundle.
 *
 * This deliberately uses a tiny ExtensionAPI double rather than copying the
 * loop-breaker algorithm into Python. The replay therefore tests the artifact
 * that contributors install, with no model or network involved.
 *
 * It imports `packages/engine/engine.ts` directly — the shipped engine
 * bundle, whose default export registers both guards (loop-breaker and
 * preserve-symbols) on `tool_call`. On the loop fixtures the calls are
 * `bash ls -R`, which preserve-symbols (edit-only) ignores, so the expected
 * block counts are unchanged from the standalone-loop-breaker era.
 *
 * **Fixtures must contain only calls that reach `beforeToolCall`.** Pi
 * validates tool arguments at `agent-loop.js:404` and invokes the hook on the
 * next line, inside the same `try` — so a call that fails schema validation
 * throws one line above every `tool_call` guard and is never seen by one.
 * A fixture extracted naively from `tool_execution_start` includes those
 * calls, and this harness will then report a guard blocking a loop it cannot
 * reach live. That happened: a fixture of 57 calls reported 44 blocks from
 * call 7, and the live run blocked nothing, because 52 of the 57 were
 * validation failures. Filter on the `tool_execution_end` result: drop
 * anything beginning "Validation failed for tool".
 */

import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const extensionPath = resolve(root, "packages/engine/engine.ts");
const { default: guards } = await import(pathToFileURL(extensionPath));

function loadFixture(contents) {
	return JSON.parse(contents);
}

async function replay(fixture) {
	const handlers = new Map();
	const entries = [];
	guards({
		on(event, handler) {
			handlers.set(event, handler);
		},
		appendEntry(customType, data) {
			entries.push({ customType, data });
		},
	});

	const handler = handlers.get("tool_call");
	if (!handler) throw new Error("guards did not register tool_call");

	let blocked = 0;
	// 1-based index of the first refusal. A blocked count alone cannot
	// tell a guard that fires early from one that fires after the model
	// has already wasted most of its budget, and "how soon" is the whole
	// value of a loop breaker.
	let firstBlock = null;
	let index = 0;
	for (const call of fixture.calls) {
		index += 1;
		if (await handler(call)) {
			blocked += 1;
			if (firstBlock === null) firstBlock = index;
		}
	}
	return { calls: fixture.calls.length, blocked, firstBlock, entries };
}

const fixturePaths = process.argv.slice(2);
if (fixturePaths.length === 0) {
	console.error("usage: node --experimental-strip-types tools/replay_guards.mjs <fixture.json>");
	process.exit(2);
}

for (const fixturePath of fixturePaths) {
	const fixture = loadFixture(await readFile(resolve(fixturePath), "utf8"));
	const result = await replay(fixture);
	const expected = fixture.expected;
	const mismatches = [];
	if (result.blocked !== expected.blocked)
		mismatches.push(`blocked ${result.blocked} != ${expected.blocked}`);
	if (result.entries.length !== expected.entries)
		mismatches.push(`entries ${result.entries.length} != ${expected.entries}`);
	if ("firstBlock" in expected && result.firstBlock !== expected.firstBlock)
		mismatches.push(`firstBlock ${result.firstBlock} != ${expected.firstBlock}`);
	if (mismatches.length > 0) {
		throw new Error(`${fixture.name}: ${mismatches.join("; ")}`);
	}
	console.log(
		JSON.stringify({
			name: fixture.name,
			calls: result.calls,
			blocked: result.blocked,
			firstBlock: result.firstBlock,
			entries: result.entries.length,
		}),
	);
}
