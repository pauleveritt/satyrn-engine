import assert from "node:assert/strict";
import test from "node:test";

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
});

const input = () => ({
	path: "src/app.py",
	edits: [{ oldText: "return 1", newText: "return 2" }],
});

const success = (revision = SECOND_REVISION) => ({
	version: 1,
	ok: true,
	code: "OK",
	message: "",
	result: { path: "src/app.py", sha256: revision },
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
		content: [{ type: "text", text: `Replaced src/app.py; sha256=${SECOND_REVISION}` }],
		details: {
			satyrn: true,
			ok: true,
			code: "OK",
			result: { path: "src/app.py", sha256: SECOND_REVISION },
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
	]) {
		const response = await mutator.execute("call", candidate);
		assert.equal(response.details.ok, false);
	}
	assert.equal(exchanges, 0);
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
			result: { path: "other.py", sha256: SECOND_REVISION },
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
	let tool;
	let resultHandler;
	return {
		api: {
			registerTool(candidate) {
				tool = candidate;
			},
			on(event, handler) {
				assert.equal(event, "tool_result");
				resultHandler = handler;
			},
		},
		get tool() {
			return tool;
		},
		get resultHandler() {
			return resultHandler;
		},
	};
}

test("registered tool exposes one replacement and marks refusals as errors", async () => {
	const pi = fakePi();
	registerMutator(pi.api, context(), async () => success());

	assert.equal(pi.tool.name, "edit");
	assert.equal(pi.tool.parameters.properties.edits.maxItems, 1);
	const response = await pi.tool.execute("call", input());
	assert.equal(response.details.ok, true);
	assert.equal(
		await pi.resultHandler({ toolName: "edit", details: response.details }),
		undefined,
	);
	assert.deepEqual(
		await pi.resultHandler({
			toolName: "edit",
			details: { satyrn: true, ok: false },
		}),
		{ isError: true },
	);
	assert.equal(await pi.resultHandler({ toolName: "read", details: null }), undefined);
});

test("default extension leaves built-in edit alone without explicit context", () => {
	const previousContext = process.env.SATYRN_MUTATION_CONTEXT;
	const previousRepo = process.env.SATYRN_ENGINE_REPO;
	delete process.env.SATYRN_MUTATION_CONTEXT;
	delete process.env.SATYRN_ENGINE_REPO;
	try {
		const pi = fakePi();
		mutationExtension(pi.api);
		assert.equal(pi.tool, undefined);
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
		const pi = fakePi();
		mutationExtension(pi.api);
		assert.equal(pi.tool, undefined);
	} finally {
		if (previousContext === undefined) delete process.env.SATYRN_MUTATION_CONTEXT;
		else process.env.SATYRN_MUTATION_CONTEXT = previousContext;
		if (previousRepo === undefined) delete process.env.SATYRN_ENGINE_REPO;
		else process.env.SATYRN_ENGINE_REPO = previousRepo;
	}
});

test("default extension registers only from a valid explicit context", async () => {
	const pi = fakePi();
	mutationExtension(
		pi.api,
		{
			SATYRN_MUTATION_CONTEXT: JSON.stringify(context()),
			SATYRN_ENGINE_REPO: "/engine",
		},
		async () => success(),
	);

	assert.equal(pi.tool.name, "edit");
	assert.equal((await pi.tool.execute("call", input())).details.ok, true);
});

test("default extension prepares the production transport from valid context", () => {
	const pi = fakePi();
	mutationExtension(pi.api, {
		SATYRN_MUTATION_CONTEXT: JSON.stringify(context()),
		SATYRN_ENGINE_REPO: "/engine",
	});

	assert.equal(pi.tool.name, "edit");
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
					assert.equal(event, "data");
					dataHandler = handler;
				},
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
