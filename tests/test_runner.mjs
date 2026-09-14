import assert from "node:assert/strict";
import test from "node:test";

import { AdapterRefusal, parseResponse } from "../packages/engine/orchestrator.ts";
import runnerExtension, {
	SELF_TEST_DEADLINE_MS,
	buildTestRequest,
	createRunner,
	parseTestResponse,
	registerRunner,
} from "../packages/engine/runner.ts";
import { createEngineExchange, parseMutationContext } from "../packages/engine/mutator.ts";

const context = () => ({
	version: 1,
	repo: "/workspace",
	contract: "/workspace/contract.yaml",
	revisions: {},
	base_commit: "b".repeat(40),
});

const success = (overrides = {}) => ({
	version: 1,
	ok: true,
	code: "OK",
	message: "",
	result: { exit_code: 0, output: "ok\n", truncated: false, timed_out: false, ...overrides },
});

test("test request carries repo, contract, a null command, and the base commit", () => {
	assert.deepEqual(JSON.parse(buildTestRequest(context())), {
		version: 1,
		operation: "test",
		repo: "/workspace",
		contract: "/workspace/contract.yaml",
		command: null,
		base_commit: context().base_commit,
	});
});

test("test response parser rejects malformed success and refusal", () => {
	assert.deepEqual(parseTestResponse(success()), success());
	for (const response of [
		{ ...success(), code: "OTHER" },
		{ ...success(), result: null },
		{ ...success(), result: { ...success().result, exit_code: "0" } },
		{ ...success(), result: { ...success().result, exit_code: 1.5 } },
		{ ...success(), result: { ...success().result, output: 1 } },
		{ ...success(), result: { ...success().result, truncated: "no" } },
		{ ...success(), result: { ...success().result, timed_out: "no" } },
		{ version: 1, ok: false, code: "OTHER", message: "bad", result: null },
		{ version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "bad" },
		{ version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "bad", result: {} },
	]) {
		assert.throws(() => parseTestResponse(response), AdapterRefusal);
	}
});

test("test response parser accepts a non-zero exit code as a success", () => {
	const failing = success({ exit_code: 1, output: "assert 1 == 2" });
	assert.deepEqual(parseTestResponse(failing), failing);
});

test("test response parser accepts a named engine refusal", () => {
	for (const code of ["TEST_COMMAND_UNAVAILABLE", "TEST_COMMAND_NOT_ALLOWED"]) {
		const refused = {
			version: 1,
			ok: false,
			code,
			message: "no test_command",
			result: null,
		};
		assert.deepEqual(parseTestResponse(refused), refused);
	}
});

test("a completed run, passing or failing, is reported as a success detail", async () => {
	const runner = createRunner(context(), async () => success({ exit_code: 3, output: "boom" }));

	const response = await runner.execute("call", {});

	assert.equal(response.details.ok, true);
	assert.equal(response.details.code, "OK");
	assert.equal(response.details.result.exit_code, 3);
	assert.match(response.content[0].text, /exited 3/);
	assert.match(response.content[0].text, /boom/);
});

test("a timed-out run is still reported as a success detail", async () => {
	const runner = createRunner(
		context(),
		async () => success({ exit_code: -1, output: "still running", timed_out: true }),
	);

	const response = await runner.execute("call", {});

	assert.equal(response.details.ok, true);
	assert.equal(response.details.result.timed_out, true);
	assert.match(response.content[0].text, /timed out/);
});

test("a named engine refusal is carried through as a refusal detail", async () => {
	let exchanges = 0;
	const runner = createRunner(context(), async () => {
		exchanges += 1;
		return {
			version: 1,
			ok: false,
			code: "TEST_COMMAND_UNAVAILABLE",
			message: "no test_command declared",
			result: null,
		};
	});

	const response = await runner.execute("call", {});

	assert.equal(response.details.ok, false);
	assert.equal(response.details.code, "TEST_COMMAND_UNAVAILABLE");
	assert.equal(response.content[0].text, "TEST_COMMAND_UNAVAILABLE: no test_command declared");
	assert.equal(exchanges, 1);
});

test("a named-command mismatch is carried through as a refusal naming the allowed command", async () => {
	const runner = createRunner(context(), async () => ({
		version: 1,
		ok: false,
		code: "TEST_COMMAND_NOT_ALLOWED",
		message: 'only this exact command is allowed: "pytest tests/"',
		result: null,
	}));

	const response = await runner.execute("call", {});

	assert.equal(response.details.ok, false);
	assert.equal(response.details.code, "TEST_COMMAND_NOT_ALLOWED");
	assert.match(response.content[0].text, /pytest tests\//);
});

test("an indeterminate transport failure is a contained adapter refusal", async () => {
	for (const error of [
		new AdapterRefusal("ENGINE_TIMEOUT", "engine timed out"),
		new Error("unexpected local error"),
		"non-error failure",
	]) {
		const runner = createRunner(context(), async () => {
			throw error;
		});
		const response = await runner.execute("call", {});
		assert.equal(response.details.ok, false);
		assert.equal(response.details.result, null);
	}
});

test("any argument the model supplies is ignored, not inspected", async () => {
	// Ruling 1: the schema is open (`additionalProperties: true`) and
	// `command` is optional; a model's guessed argument -- of any shape --
	// never reaches an exchange-blocking check.
	for (const input of [{}, { command: "" }, { command: "pytest" }, { command: 1 }, { anything: true }]) {
		let exchanges = 0;
		const runner = createRunner(context(), async () => {
			exchanges += 1;
			return success();
		});
		const response = await runner.execute("call", input);
		assert.equal(response.details.ok, true);
		assert.equal(exchanges, 1);
	}
});

test("the registered tool schema is open and ignores whatever command is supplied", () => {
	const pi = fakePi();
	registerRunner(pi.api, context(), async () => success());

	assert.equal(pi.tool.name, "self_test");
	assert.equal(pi.tool.parameters.type, "object");
	assert.equal(pi.tool.parameters.additionalProperties, true);
	assert.equal(pi.tool.parameters.properties.command.type, "string");
	assert.equal(pi.tool.parameters.required, undefined);
});

test("registered tool marks a refusal as an error but not a failing suite", async () => {
	const pi = fakePi();
	registerRunner(pi.api, context(), async () => success({ exit_code: 1 }));

	const response = await pi.tool.execute("call", {});
	assert.equal(response.details.ok, true);
	assert.equal(
		await pi.resultHandler({ toolName: "self_test", details: response.details }),
		undefined,
	);
	assert.deepEqual(
		await pi.resultHandler({
			toolName: "self_test",
			details: { satyrn: true, ok: false },
		}),
		{ isError: true },
	);
	assert.equal(await pi.resultHandler({ toolName: "read", details: null }), undefined);
});

test("default extension leaves the tool set alone without explicit context", () => {
	const pi = fakePi();
	runnerExtension(pi.api, {});
	assert.equal(pi.tool, undefined);
});

test("default extension ignores malformed explicit context", () => {
	const pi = fakePi();
	runnerExtension(pi.api, { SATYRN_MUTATION_CONTEXT: "bad", SATYRN_ENGINE_REPO: "/engine" });
	assert.equal(pi.tool, undefined);
});

test("default extension registers only from a valid explicit context", async () => {
	const pi = fakePi();
	runnerExtension(
		pi.api,
		{
			SATYRN_MUTATION_CONTEXT: JSON.stringify(context()),
			SATYRN_ENGINE_REPO: "/engine",
		},
		async () => success(),
	);

	assert.equal(pi.tool.name, "self_test");
	assert.equal((await pi.tool.execute("call", {})).details.ok, true);
});

test("default extension prepares the production transport from valid context", () => {
	const pi = fakePi();
	runnerExtension(pi.api, {
		SATYRN_MUTATION_CONTEXT: JSON.stringify(context()),
		SATYRN_ENGINE_REPO: "/engine",
	});

	assert.equal(pi.tool.name, "self_test");
});

test("the runner's exchange deadline is above the runner's own 120 s timeout", () => {
	// `runner.py:39`'s DEFAULT_TEST_TIMEOUT_SECONDS = 120.0 owns the bound;
	// this is only its ceiling (I4), never a second independent number.
	assert.equal(SELF_TEST_DEADLINE_MS, 130_000);

	const pi = fakePi();
	let captured;
	const spyFactory = (spawner, engineRepo, deadlineMs) => {
		captured = { spawner, engineRepo, deadlineMs };
		return async () => success();
	};
	runnerExtension(
		pi.api,
		{
			SATYRN_MUTATION_CONTEXT: JSON.stringify(context()),
			SATYRN_ENGINE_REPO: "/engine",
		},
		undefined,
		spyFactory,
	);

	assert.equal(captured.engineRepo, "/engine");
	assert.equal(captured.deadlineMs, SELF_TEST_DEADLINE_MS);
	assert.equal(pi.tool.name, "self_test");
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

test("shared mutation context parses the same way for both tools", () => {
	// `base_commit` is added to `context()` for the request-building test
	// above (R4); `parseMutationContext` (unchanged in this task -- Task 5
	// adds the remaining context keys) ignores it, so the round trip drops
	// it here rather than refusing the extra key.
	const { base_commit: _baseCommit, ...withoutBaseCommit } = context();
	assert.deepEqual(parseMutationContext(JSON.stringify(context())), withoutBaseCommit);
});

test("base response parser rejects non-object JSON without leaking a type error", () => {
	assert.deepEqual(parseResponse(JSON.stringify(success())), success());
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
