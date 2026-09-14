import assert from "node:assert/strict";
import test from "node:test";
import { createHash } from "node:crypto";

import { AdapterRefusal, parseResponse } from "../packages/engine/orchestrator.ts";
import mutationExtension, {
	buildReplacementRequest,
	createEngineExchange,
	createMutator,
	parseMutationContext,
	parseReplacementResponse,
	registerMutator,
} from "../packages/engine/mutator.ts";

const FIRST_REVISION = "1".repeat(64);
const SECOND_REVISION = "2".repeat(64);

const context = () => ({
	version: 1,
	repo: "/workspace",
	contract: "/workspace/contract.yaml",
	revisions: { "src/app.py": FIRST_REVISION },
	writable_paths: ["src/*"],
	test_command: ["uv", "run", "python", "-m", "pytest", "-q"],
	symbols: {},
	carried: [],
	base_commit: "b".repeat(40),
});

const input = () => ({
	path: "src/app.py",
	edits: [{ oldText: "return 1", newText: "return 2" }],
});

const success = (revision = SECOND_REVISION, region = "1: return 2") => ({
	version: 1,
	ok: true,
	code: "OK",
	message: "",
	result: { path: "src/app.py", sha256: revision, region },
});

test("mutation context accepts one typed revision map", () => {
	assert.deepEqual(parseMutationContext(JSON.stringify(context())), context());
});

test("mutation context refuses malformed JSON and shapes", () => {
	for (const raw of [
		"{bad",
		"null",
		JSON.stringify({ ...context(), version: 2 }),
		JSON.stringify({ ...context(), repo: "relative" }),
		JSON.stringify({ ...context(), contract: "relative" }),
		JSON.stringify({ ...context(), revisions: [] }),
		JSON.stringify({ ...context(), revisions: { "": FIRST_REVISION } }),
		JSON.stringify({ ...context(), revisions: { "src/app.py": "bad" } }),
		(() => { const { writable_paths, ...rest } = context(); return JSON.stringify(rest); })(),
		JSON.stringify({ ...context(), symbols: [] }),
		JSON.stringify({ ...context(), test_command: [1] }),
		(() => { const { carried, ...rest } = context(); return JSON.stringify(rest); })(),
		JSON.stringify({ ...context(), base_commit: "not-hex" }),
	]) {
		assert.throws(() => parseMutationContext(raw), AdapterRefusal);
	}
});

test("replacement request carries policy data without applying policy", () => {
	assert.deepEqual(JSON.parse(buildReplacementRequest(context(), input(), FIRST_REVISION)), {
		version: 1,
		operation: "replace",
		repo: "/workspace",
		contract: "/workspace/contract.yaml",
		path: "src/app.py",
		expected_sha256: FIRST_REVISION,
		old_text: "return 1",
		new_text: "return 2",
	});
	assert.equal(
		JSON.parse(buildReplacementRequest(context(), input(), null)).expected_sha256,
		null,
	);
});

test("success advances the revision used by the next request", async () => {
	const requests = [];
	const responses = [success(SECOND_REVISION), success("3".repeat(64))];
	const mutator = createMutator(context(), async (request) => {
		requests.push(JSON.parse(request));
		return responses.shift();
	});

	const first = await mutator.execute("first", input());
	const second = await mutator.execute("second", input());

	assert.deepEqual(first, {
		// E9(a): the model-facing text is the post-edit region computed by
		// the Python core, not a hash line -- see mutator.ts's
		// `successResult`. This replaces the prior
		// `Replaced src/app.py; sha256=...` assertion, which encoded the
		// hash-only message this change corrects.
		content: [{ type: "text", text: "1: return 2" }],
		details: {
			satyrn: true,
			ok: true,
			code: "OK",
			result: { path: "src/app.py", sha256: SECOND_REVISION, region: "1: return 2" },
		},
	});
	assert.equal(second.details.ok, true);
	assert.equal(requests[0].expected_sha256, FIRST_REVISION);
	assert.equal(requests[1].expected_sha256, SECOND_REVISION);
});

test("a refusal does not advance the revision", async () => {
	const requests = [];
	const mutator = createMutator(context(), async (request) => {
		requests.push(JSON.parse(request));
		return {
			version: 1,
			ok: false,
			code: "ANCHOR_MISSING",
			message: "old_text was not found",
			result: null,
		};
	});

	const first = await mutator.execute("first", input());
	const second = await mutator.execute("second", input());

	assert.equal(first.details.ok, false);
	assert.equal(first.content[0].text, "ANCHOR_MISSING: old_text was not found");
	assert.equal(second.details.ok, false);
	assert.equal(requests[0].expected_sha256, FIRST_REVISION);
	assert.equal(requests[1].expected_sha256, FIRST_REVISION);
});

test("a result-less invalid request is determinate and does not poison", async () => {
	let exchanges = 0;
	const mutator = createMutator(context(), async () => {
		exchanges += 1;
		return {
			version: 1,
			ok: false,
			code: "INVALID_REQUEST",
			message: "invalid replacement path",
		};
	});

	const first = await mutator.execute("first", input());
	const second = await mutator.execute("second", input());

	assert.equal(first.details.code, "INVALID_REQUEST");
	assert.equal(first.details.result, null);
	assert.equal(second.details.code, "INVALID_REQUEST");
	assert.equal(exchanges, 2);
});

test("missing revision reaches the engine as an explicit null", async () => {
	const requests = [];
	const missingContext = { ...context(), revisions: {} };
	const mutator = createMutator(missingContext, async (request) => {
		requests.push(JSON.parse(request));
		return {
			version: 1,
			ok: false,
			code: "REVISION_UNAVAILABLE",
			message: "no captured revision is available",
			result: null,
		};
	});

	const first = await mutator.execute("first", input());
	const second = await mutator.execute("second", input());

	assert.equal(first.details.code, "REVISION_UNAVAILABLE");
	assert.equal(second.details.code, "REVISION_UNAVAILABLE");
	assert.deepEqual(requests.map((request) => request.expected_sha256), [null, null]);
});

test("a successful native write moves the path's revision so a later edit sends the written digest", async () => {
	const requests = [];
	const mutator = createMutator(context(), async (request) => { requests.push(JSON.parse(request)); return success(); });
	mutator.noteWrite("src/app.py", "def value():\n    return 1\n");
	await mutator.execute("1", input());
	assert.equal(requests[0].expected_sha256, createHash("sha256").update("def value():\n    return 1\n", "utf8").digest("hex"));
});

test("a write by absolute or @ path and an edit by relative path share one revision key", async () => {
	const requests = [];
	const mutator = createMutator(context(), async (request) => { requests.push(JSON.parse(request)); return success(); });
	mutator.noteWrite("/workspace/src/app.py", "x = 1\n");
	await mutator.execute("1", input());
	mutator.noteWrite("@src/app.py", "x = 2\n");
	await mutator.execute("2", input());
	mutator.noteWrite("/elsewhere/app.py", "ignored");   // outside the repo: no key moves
	await mutator.execute("3", input());
	assert.deepEqual(requests.map((r) => r.expected_sha256), [
		createHash("sha256").update("x = 1\n", "utf8").digest("hex"),
		createHash("sha256").update("x = 2\n", "utf8").digest("hex"),
		SECOND_REVISION,   // the mutator's own last success
	]);
});

test("a write result with isError leaves the revision alone, and a new file written then edited is not REVISION_UNAVAILABLE", async () => {
	const requests = [];
	const { pi, handlers } = fakePi();
	registerMutator(pi, context(), async (request) => { requests.push(JSON.parse(request)); return success(); });
	await toolResult(handlers.tool_result, { toolName: "write", isError: true, input: { path: "src/app.py", content: "junk" }, content: [], details: undefined });
	await toolResult(handlers.tool_result, { toolName: "write", isError: false, input: { path: "src/new.py", content: "x = 1\n" }, content: [], details: undefined });
	const tool = registeredTools(pi).edit;   // the fake pi records registerTool calls
	await tool.execute("1", input());
	await tool.execute("2", { path: "src/new.py", edits: [{ oldText: "x = 1", newText: "x = 2" }] });
	assert.equal(requests[0].expected_sha256, FIRST_REVISION);
	assert.equal(requests[1].expected_sha256, createHash("sha256").update("x = 1\n", "utf8").digest("hex"));
});

test("malformed input refuses before exchange", async () => {
	let exchanges = 0;
	const mutator = createMutator(context(), async () => {
		exchanges += 1;
		return success();
	});

	for (const candidate of [
		null,
		{ ...input(), path: "" },
		{ ...input(), edits: [] },
		{ ...input(), edits: [input().edits[0], input().edits[0]] },
		{ ...input(), edits: [{ oldText: "", newText: "next" }] },
		{ ...input(), edits: [{ oldText: "old", newText: 1 }] },
		{ ...input(), edits: [{ oldText: "old", newText: "new", path: 7 }] },
	]) {
		const response = await mutator.execute("call", candidate);
		assert.equal(response.details.ok, false);
	}
	assert.equal(exchanges, 0);
});

test("a redundant item path that matches is accepted, and reaches the engine once", async () => {
	// The success sibling for the refusal below, and the regression test for
	// the 2026-09-06 V13 probe: 973 `edits.0: must not have additional
	// properties` refusals, on a key equal to the top-level path in 701 of
	// 701 recovered calls.
	const requests = [];
	const mutator = createMutator(context(), async (request) => {
		requests.push(JSON.parse(request));
		return success();
	});

	const response = await mutator.execute("call", {
		path: "src/app.py",
		edits: [{ oldText: "return 1", newText: "return 2", path: "src/app.py" }],
	});

	assert.equal(response.details.ok, true);
	assert.equal(requests.length, 1);
	assert.equal(requests[0].path, "src/app.py");
	assert.equal(requests[0].old_text, "return 1");
});

test("an item path that contradicts the file path is refused before exchange", async () => {
	let exchanges = 0;
	const mutator = createMutator(context(), async () => {
		exchanges += 1;
		return success();
	});

	const response = await mutator.execute("call", {
		path: "src/app.py",
		edits: [{ oldText: "return 1", newText: "return 2", path: "src/other.py" }],
	});

	assert.equal(response.details.ok, false);
	assert.equal(response.details.code, "INVALID_REQUEST");
	// Asserted on the text the model actually receives, not on `details`:
	// a refusal the model cannot read is what produced the 973-call loop.
	assert.match(response.content[0].text, /does not match the file path/);
	assert.equal(exchanges, 0);
});

test("the edit item tolerates path and nothing else", () => {
	// Pins the shape against the two ways this fix could be widened by a
	// later tidy: dropping `additionalProperties` on the item, or leaving
	// `path` out again.
	const { pi } = fakePi();
	registerMutator(pi, context(), async () => success());
	const items = registeredTools(pi).edit.parameters.properties.edits.items;

	assert.equal(items.additionalProperties, false);
	assert.deepEqual(Object.keys(items.properties).sort(), ["newText", "oldText", "path"]);
	assert.deepEqual(items.required, ["oldText", "newText"]);
});

test("indeterminate engine outcomes poison the mutation context", async () => {
	for (const error of [
		new AdapterRefusal("ENGINE_TIMEOUT", "engine timed out"),
		new AdapterRefusal("ENGINE_CRASHED", "engine crashed"),
		new AdapterRefusal("ENGINE_START_FAILED", "engine failed to start"),
		new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response was malformed"),
		new Error("unexpected local error"),
		"non-error failure",
	]) {
		let exchanges = 0;
		const mutator = createMutator(context(), async () => {
			exchanges += 1;
			throw error;
		});
		const first = await mutator.execute("first", input());
		const second = await mutator.execute("second", input());
		assert.equal(first.details.ok, false);
		assert.equal(first.details.result, null);
		assert.equal(second.details.code, "MUTATION_CONTEXT_POISONED");
		assert.equal(second.details.result, null);
		assert.equal(exchanges, 1);
	}
});

test("base response parser rejects non-object JSON without leaking a type error", () => {
	assert.deepEqual(parseResponse(JSON.stringify(success())), success());
	for (const text of [
		"null",
		"[]",
		"{bad",
		'{"version":1}',
		'{"version":1,"ok":true,"code":"OTHER","message":""}',
		'{"version":1,"ok":false,"code":"OK","message":""}',
		'{"version":1,"ok":false,"code":"OTHER","message":""}',
	]) {
		assert.throws(() => parseResponse(text), AdapterRefusal);
	}
});

test("replacement response parser rejects malformed success and refusal", () => {
	assert.deepEqual(parseReplacementResponse(success()), success());
	for (const response of [
		{ ...success(), code: "OTHER" },
		{ ...success(), result: null },
		{ ...success(), result: { path: 1, sha256: SECOND_REVISION } },
		{ ...success(), result: { path: "src/app.py", sha256: "bad" } },
		{ version: 1, ok: false, code: "OTHER", message: "missing", result: null },
		{ version: 1, ok: false, code: "ANCHOR_MISSING", message: "missing" },
		{ version: 1, ok: false, code: "ANCHOR_MISSING", message: "missing", result: {} },
	]) {
		assert.throws(() => parseReplacementResponse(response), AdapterRefusal);
	}
});

test("a mismatched successful path is a contained malformed response", async () => {
	let exchanges = 0;
	const mutator = createMutator(context(), async () => {
		exchanges += 1;
		return {
			...success(),
			result: { path: "other.py", sha256: SECOND_REVISION, region: "1: return 2" },
		};
	});

	const response = await mutator.execute("first", input());
	const poisoned = await mutator.execute("second", input());

	assert.equal(response.details.code, "ENGINE_MALFORMED_RESPONSE");
	assert.equal(response.details.ok, false);
	assert.equal(poisoned.details.code, "MUTATION_CONTEXT_POISONED");
	assert.equal(exchanges, 1);
});

test("a result-less policy refusal is malformed and poisons", async () => {
	let exchanges = 0;
	const mutator = createMutator(context(), async () => {
		exchanges += 1;
		return { version: 1, ok: false, code: "ANCHOR_MISSING", message: "missing" };
	});

	const response = await mutator.execute("first", input());
	const poisoned = await mutator.execute("second", input());

	assert.equal(response.details.code, "ENGINE_MALFORMED_RESPONSE");
	assert.equal(poisoned.details.code, "MUTATION_CONTEXT_POISONED");
	assert.equal(exchanges, 1);
});

function fakePi() {
	const handlers = {};
	const entries = [];
	const tools = {};
	const pi = {
		registerTool(candidate) {
			tools[candidate.name] = candidate;
		},
		on(event, handler) {
			(handlers[event] ??= []).push(handler);
		},
		async appendEntry(kind, data) {
			entries.push({ kind, data });
		},
		_tools: tools,
	};
	return { pi, handlers, entries };
}

function registeredTools(pi) {
	return pi._tools;
}

/** Calls every registered `tool_result` handler in turn and returns the
 * first defined result. `registerMutator` registers two `tool_result`
 * listeners (a write-note listener and an edit-details listener); each
 * returns `undefined` for the other's event, so calling every handler this
 * way -- rather than only `handlers.tool_result[0]` -- is order-independent
 * and matches how Pi actually dispatches a result to every listener. */
async function toolResult(handlers, event) {
	for (const handler of handlers) {
		const outcome = await handler(event);
		if (outcome !== undefined) return outcome;
	}
	return undefined;
}

test("registered tool exposes one replacement and marks refusals as errors", async () => {
	const { pi, handlers } = fakePi();
	registerMutator(pi, context(), async () => success());

	const tool = registeredTools(pi).edit;
	assert.equal(tool.name, "edit");
	assert.equal(tool.parameters.properties.edits.maxItems, 1);
	const response = await tool.execute("call", input());
	assert.equal(response.details.ok, true);
	assert.equal(await toolResult(handlers.tool_result, { toolName: "edit", details: response.details }), undefined);
	assert.deepEqual(
		await toolResult(handlers.tool_result, { toolName: "edit", details: { satyrn: true, ok: false } }),
		{ isError: true },
	);
	assert.equal(await toolResult(handlers.tool_result, { toolName: "read", details: null }), undefined);
});

test("default extension leaves built-in edit alone without explicit context", () => {
	const previousContext = process.env.SATYRN_MUTATION_CONTEXT;
	const previousRepo = process.env.SATYRN_ENGINE_REPO;
	delete process.env.SATYRN_MUTATION_CONTEXT;
	delete process.env.SATYRN_ENGINE_REPO;
	try {
		const { pi } = fakePi();
		mutationExtension(pi);
		assert.equal(registeredTools(pi).edit, undefined);
	} finally {
		if (previousContext === undefined) delete process.env.SATYRN_MUTATION_CONTEXT;
		else process.env.SATYRN_MUTATION_CONTEXT = previousContext;
		if (previousRepo === undefined) delete process.env.SATYRN_ENGINE_REPO;
		else process.env.SATYRN_ENGINE_REPO = previousRepo;
	}
});

test("default extension ignores malformed explicit context", () => {
	const previousContext = process.env.SATYRN_MUTATION_CONTEXT;
	const previousRepo = process.env.SATYRN_ENGINE_REPO;
	process.env.SATYRN_MUTATION_CONTEXT = "bad";
	process.env.SATYRN_ENGINE_REPO = "/engine";
	try {
		const { pi } = fakePi();
		mutationExtension(pi);
		assert.equal(registeredTools(pi).edit, undefined);
	} finally {
		if (previousContext === undefined) delete process.env.SATYRN_MUTATION_CONTEXT;
		else process.env.SATYRN_MUTATION_CONTEXT = previousContext;
		if (previousRepo === undefined) delete process.env.SATYRN_ENGINE_REPO;
		else process.env.SATYRN_ENGINE_REPO = previousRepo;
	}
});

test("default extension registers only from a valid explicit context", async () => {
	const { pi } = fakePi();
	mutationExtension(
		pi,
		{
			SATYRN_MUTATION_CONTEXT: JSON.stringify(context()),
			SATYRN_ENGINE_REPO: "/engine",
		},
		async () => success(),
	);

	const tool = registeredTools(pi).edit;
	assert.equal(tool.name, "edit");
	assert.equal((await tool.execute("call", input())).details.ok, true);
});

test("default extension prepares the production transport from valid context", () => {
	const { pi } = fakePi();
	mutationExtension(pi, {
		SATYRN_MUTATION_CONTEXT: JSON.stringify(context()),
		SATYRN_ENGINE_REPO: "/engine",
	});

	assert.equal(registeredTools(pi).edit.name, "edit");
});

test("engine exchange factory delegates to the existing one-shot transport", async () => {
	let requestText;
	const spawner = (_command, _args, options) => {
		assert.equal(options.cwd, "/engine");
		let dataHandler;
		let closeHandler;
		return {
			stdin: {
				write(text) {
					requestText = text;
				},
				end() {
					queueMicrotask(() => {
						dataHandler(JSON.stringify(success()));
						closeHandler(0);
					});
				},
			},
			stdout: {
				on(event, handler) {
					if (event === "data") dataHandler = handler;
				},
			},
			stderr: {
				on() {},
			},
			on(event, handler) {
				if (event === "close") closeHandler = handler;
			},
			kill() {},
		};
	};
	const transport = createEngineExchange(spawner, "/engine", 1000);

	assert.deepEqual(await transport("request"), success());
	assert.equal(requestText, "request");
});

// --- 2026-09-09: the model-facing prose must match the tool ---
//
// pi lists a tool under "Available tools" only when its registration supplies
// a promptSnippet (system-prompt.js: visibleTools filters on it). The engine
// supplied none, so its bounded `edit` -- which overrides pi's built-in of the
// same name -- was described to the model by whatever pi says about the
// built-in, or omitted. The schemas were always right; the prose was not.

test("the bounded edit registers a prompt snippet naming its restriction", () => {
	const registered = [];
	registerMutator(
		{ registerTool: (tool) => registered.push(tool), on: () => undefined },
		context(),
		async () => success(),
	);
	const edit = registered.find((tool) => tool.name === "edit");
	assert.ok(edit, "the edit tool is registered");
	assert.equal(typeof edit.promptSnippet, "string");
	assert.match(
		edit.promptSnippet,
		/anchor/i,
		"the snippet must say what makes this edit different from pi's built-in",
	);
});
