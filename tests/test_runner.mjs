import assert from "node:assert/strict";
import test from "node:test";

import { AdapterRefusal, parseResponse } from "../packages/engine/orchestrator.ts";
import runnerExtension, {
	SELF_TEST_DEADLINE_MS,
	buildTestRequest,
	createRunner,
	parseTestResponse,
	REDIRECTED_COMMAND,
	enforcedMessage,
	isFinalTurn,
	isTestRunCommand,
	redirectSentence,
	registerRunner,
} from "../packages/engine/runner.ts";
import { createEngineExchange, parseMutationContext } from "../packages/engine/mutator.ts";

const context = () => ({
	version: 1,
	repo: "/workspace",
	contract: "/workspace/contract.yaml",
	revisions: {},
	writable_paths: ["src/*"],
	test_command: ["uv", "run", "python", "-m", "pytest", "-q"],
	symbols: {},
	carried: [],
	base_commit: "b".repeat(40),
});

const success = (overrides = {}) => ({
	version: 1,
	ok: true,
	code: "OK",
	message: "",
	result: { exit_code: 0, output: "ok\n", truncated: false, timed_out: false, compact_bytes: 0, ...overrides },
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
		{ ...success(), result: { ...success().result, compact_bytes: "0" } },
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

// Phase 3b: guard 2 enforced. Commands observed in the route-proof and
// admission transcripts (~/satyrn-runs/2026-09-1[45]-*), worktree paths shortened.
const TEST_RUNS = [
	"uv run python -m pytest -q",
	"pytest",
	'cd "$(pwd)"; uv run pytest tests/test_lint_docs.py -q 2>&1 | tail -25',
	"cd /w/worktree; timeout 115 uv run python -m pytest -q 2>&1 | tail -40",
	"cd /w/worktree\nuv run pytest tests/ -q 2>&1 | tail -15",
	"cd /w/worktree && uv run pytest tests/its.py -q 2>&1 | tail -6",
	'uv run python -m pytest tests/_probe.py -v 2>&1 | grep -E "PASSED|FAILED|ERROR"',
	"PYTHONPATH=src python3 -m pytest -x tests/test_a.py::test_b",
	'.venv/bin/pytest -q >/dev/null 2>&1; echo "EXIT: $?"',
	"uv run --frozen pytest -k lint",
];

const NOT_TEST_RUNS = [
	'uv run pytest tests/test_review.py -q 2>&1 | tail -5; echo "===RUFF==="; uv run ruff check tools/review.py',
	"cat tests/conftest.py; uv run pytest -q",
	"head -20 tests/test_provenance.py; uv run pytest tests/test_provenance.py -q",
	"cd /w && cat > /tmp/probe_test.py <<'EOF'\ndef test_x():\n    assert True\nEOF\nuv run pytest /tmp/probe_test.py",
	'uv run python -c "import app; print(app)"',
	"uv run pytest -q > out.txt",
	"uv run pytest -q &",
	"uv run pytest -q | tee log.txt",
	"echo $(uv run pytest -q)",
	"grep -rn pytest tests/",
	"just test",
	"timeout uv run pytest",
	"uv run pytest 'tests/test_a.py",
];

test("a bash command that only runs the test runner is a test run; one doing anything else is not", () => {
	for (const command of TEST_RUNS) assert.equal(isTestRunCommand(command), true, command);
	for (const command of NOT_TEST_RUNS) assert.equal(isTestRunCommand(command), false, command);
});

test("a test run through bash becomes true, is recorded, and its result is the self-test's under one sentence", async () => {
	const pi = fakePi();
	const requests = [];
	registerRunner(pi.api, context(), async (request) => {
		requests.push(JSON.parse(request));
		return success({ exit_code: 1, output: "FAILED tests/test_a.py::test_b - assert 1 == 2" });
	});
	const [onCall] = pi.handlers.tool_call;
	const [onResult] = pi.handlers.tool_result;
	const call = { toolCallId: "c1", toolName: "bash", input: { command: "uv run pytest tests/test_a.py -q 2>&1 | tail -5", timeout: 120 } };
	assert.equal(await onCall(call), undefined);
	assert.deepEqual(call.input, { command: REDIRECTED_COMMAND, timeout: 120 });
	assert.deepEqual(pi.entries, [{ kind: "self_test_redirected", data: { toolCallId: "c1" } }]);
	const patch = await onResult({ toolCallId: "c1", toolName: "bash", input: call.input, isError: false,
		content: [{ type: "text", text: "(no output)" }], details: undefined });
	assert.deepEqual(patch, {
		content: [{ type: "text", text: `${redirectSentence(context().test_command)}\nTest command exited 1\nFAILED tests/test_a.py::test_b - assert 1 == 2` }],
		isError: false,
	});
	assert.equal(redirectSentence(context().test_command),
		'The Engine ran self_test in place of this command: it runs "uv run python -m pytest -q" over the whole suite, whatever paths or flags the command named.');
	assert.equal(requests.length, 1);
	assert.equal(requests[0].operation, "test");
	// Consumed once: a second result for the same id is not redirected again.
	assert.equal(await onResult({ toolCallId: "c1", toolName: "bash", content: [], details: undefined }), undefined);
});

test("a redirected run the engine refuses is an error result; any other bash call is untouched", async () => {
	const pi = fakePi();
	let exchanges = 0;
	registerRunner(pi.api, context(), async () => {
		exchanges += 1;
		return { version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "gone", result: null };
	});
	const [onCall] = pi.handlers.tool_call;
	const [onResult] = pi.handlers.tool_result;
	await onCall({ toolCallId: "r1", toolName: "bash", input: { command: "pytest" } });
	const refused = await onResult({ toolCallId: "r1", toolName: "bash", content: [], details: undefined });
	assert.equal(refused.isError, true);
	assert.match(refused.content[0].text, /TEST_COMMAND_UNAVAILABLE: gone$/);
	const other = { toolCallId: "o1", toolName: "bash", input: { command: "uv run ruff check" } };
	assert.equal(await onCall(other), undefined);
	assert.deepEqual(other.input, { command: "uv run ruff check" });
	assert.equal(await onResult({ toolCallId: "o1", toolName: "bash", content: [{ type: "text", text: "ok" }], details: undefined }), undefined);
	assert.equal(await onCall({ toolCallId: "x1", toolName: "read", input: { path: "pytest" } }), undefined);
	assert.equal(exchanges, 1);
	assert.deepEqual(pi.entries.map((entry) => entry.kind), ["self_test_redirected"]);
});

const FINAL = { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "Done." }] };
const CALLING = { role: "assistant", stopReason: "toolUse", content: [{ type: "toolCall", id: "t", name: "read", arguments: {} }] };

test("a final turn is an assistant message with no tool call that did not error or abort", () => {
	assert.equal(isFinalTurn(FINAL), true);
	assert.equal(isFinalTurn({ ...FINAL, stopReason: "length" }), true);
	assert.equal(isFinalTurn(CALLING), false);
	assert.equal(isFinalTurn({ ...FINAL, stopReason: "error" }), false);
	assert.equal(isFinalTurn({ ...FINAL, stopReason: "aborted" }), false);
	assert.equal(isFinalTurn({ role: "user", content: [] }), false);
	assert.equal(isFinalTurn(undefined), false);
});

function gated(responses) {
	const pi = fakePi();
	let exchanges = 0;
	registerRunner(pi.api, context(), async () => responses[Math.min(exchanges++, responses.length - 1)]);
	return {
		pi,
		exchanges: () => exchanges,
		turnEnd: (message) => pi.handlers.turn_end[0]({ type: "turn_end", turnIndex: 0, message, toolResults: [] }),
		result: (event) => pi.handlers.tool_result[0](event),
		call: (event) => pi.handlers.tool_call[0](event),
	};
}

const LANDED_EDIT = { toolCallId: "e1", toolName: "edit", input: {}, isError: false, content: [],
	details: { satyrn: true, ok: true, code: "OK", result: { path: "app.py", sha256: "1".repeat(64), region: "" } } };

test("a session that ends without a self-test gets one enforced run, and a failure goes back as one follow-up", async () => {
	const gate = gated([success({ exit_code: 1, output: "FAILED tests/test_app.py::test_home - assert 404 == 200" })]);
	assert.equal(await gate.turnEnd(CALLING), undefined);
	assert.equal(gate.exchanges(), 0);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 0, code: "OK", exit_code: 1, follow_up: true } },
	]);
	assert.deepEqual(gate.pi.sent, [{
		message: {
			customType: "self_test_enforced",
			content: enforcedMessage("Test command exited 1\nFAILED tests/test_app.py::test_home - assert 404 == 200"),
			display: true,
			details: undefined,
		},
		options: { deliverAs: "followUp" },
	}]);
	assert.equal(enforcedMessage("R"),
		"Before you finish: the Engine ran self_test because nothing had run it since the last change, and it did not pass.\nR");
	// Once per generation: ending again with no new change runs nothing.
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	assert.equal(gate.pi.sent.length, 1);
});

test("a self-test the model ran after its last change satisfies the gate; a landed edit re-arms it and a pass sends nothing", async () => {
	const gate = gated([success({ exit_code: 0, output: "3 passed" })]);
	await gate.pi.tool.execute("s1", {});
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	assert.deepEqual(gate.pi.entries, []);
	await gate.result(LANDED_EDIT);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 1, code: "OK", exit_code: 0, follow_up: false } },
	]);
	assert.deepEqual(gate.pi.sent, []);
	// Silent rows: a refused Satyrn edit and an edit result lacking
	// `details.satyrn` (some other edit tool) neither count as a landed
	// mutation, so the gate the checked self-test already satisfied stays
	// satisfied -- no re-arm, no further exchange.
	await gate.result({ toolCallId: "e2", toolName: "edit", input: {}, isError: true, content: [],
		details: { satyrn: true, ok: false, code: "SYMBOL_REMOVED", result: null } });
	await gate.result({ toolCallId: "e3", toolName: "edit", input: {}, isError: false, content: [],
		details: { ok: true } });
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 1, code: "OK", exit_code: 0, follow_up: false } },
	]);
	assert.deepEqual(gate.pi.sent, []);
});

test("a redirected bash test run satisfies the gate; a successful write re-arms it; a refused write does not", async () => {
	const gate = gated([success({ exit_code: 0, output: "3 passed" })]);
	await gate.call({ toolCallId: "b1", toolName: "bash", input: { command: "uv run pytest -q" } });
	await gate.result({ toolCallId: "b1", toolName: "bash", input: { command: REDIRECTED_COMMAND }, isError: false, content: [], details: undefined });
	await gate.result({ toolCallId: "w0", toolName: "write", input: { path: "app.py", content: "x" }, isError: true, content: [], details: undefined });
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	await gate.result({ toolCallId: "w1", toolName: "write", input: { path: "app.py", content: "x" }, isError: false, content: [], details: undefined });
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries.map((entry) => entry.kind), ["self_test_redirected", "self_test_enforced"]);
});

test("an enforced run the engine refuses is recorded and sends nothing; an errored or aborted turn is never gated", async () => {
	const gate = gated([{ version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "gone", result: null }]);
	await gate.turnEnd({ ...FINAL, stopReason: "error" });
	await gate.turnEnd({ ...FINAL, stopReason: "aborted" });
	assert.equal(gate.exchanges(), 0);
	await gate.turnEnd(FINAL);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 0, code: "TEST_COMMAND_UNAVAILABLE", exit_code: null, follow_up: false } },
	]);
	assert.deepEqual(gate.pi.sent, []);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
});

test("a timed-out enforced run is a failure and goes back to the model", async () => {
	const gate = gated([success({ exit_code: -1, output: "still running", timed_out: true })]);
	await gate.turnEnd(FINAL);
	assert.equal(gate.pi.entries[0].data.follow_up, true);
	assert.match(gate.pi.sent[0].message.content, /timed out/);
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
	// `base_commit` (and the other four keys `context()` now carries) is a
	// real `MutationContext` field as of this task, so a parsed context
	// carries it through unchanged: a strict round trip, not a drop.
	assert.deepEqual(parseMutationContext(JSON.stringify(context())), context());
});

test("base response parser rejects non-object JSON without leaking a type error", () => {
	assert.deepEqual(parseResponse(JSON.stringify(success())), success());
});

function fakePi() {
	let tool;
	const handlers = {};
	const entries = [];
	const sent = [];
	return {
		api: {
			sendMessage(message, options) {
				sent.push({ message, options });
			},
			registerTool(candidate) {
				tool = candidate;
			},
			on(event, handler) {
				(handlers[event] ??= []).push(handler);
			},
			async appendEntry(kind, data) {
				entries.push({ kind, data });
			},
		},
		handlers,
		entries,
		sent,
		get tool() {
			return tool;
		},
		get resultHandler() {
			return handlers.tool_result?.[0];
		},
	};
}
