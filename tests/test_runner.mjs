import assert from "node:assert/strict";
import test from "node:test";

import { AdapterRefusal, parseResponse } from "../packages/engine/orchestrator.ts";
import runnerExtension, {
	SELF_TEST_DEADLINE_MS,
	buildTestRequest,
	createRunner,
	parseTestResponse,
	DETECTED_SENTENCE,
	enforcedMessage,
	FINISH_STEER,
	hasPytestSummary,
	isFinalTurn,
	isLengthCut,
	isTestPath,
	MAX_RESUMES,
	redStopMessage,
	registerRunner,
	RESUME_MESSAGE,
} from "../packages/engine/runner.ts";
import { createEngineExchange, parseMutationContext } from "../packages/engine/mutator.ts";
import { NOTE_MARKER, fenceNote } from "../packages/engine/bounds.ts";

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

// Phase 3b re-ruled 2026-09-18: the Engine leaves the command alone and
// detects pytest's summary line in the output, then runs its own self_test
// once when a source mutation has landed since the last one.
const SOURCE_EDIT = { toolCallId: "e1", toolName: "edit", input: { path: "app.py" }, isError: false, content: [],
	details: { satyrn: true, ok: true, code: "OK", result: { path: "app.py", sha256: "1".repeat(64), region: "" } } };

test("a pytest summary line carries a numeric count and a duration on one line", () => {
	assert.equal(hasPytestSummary("1 failed, 3 passed, 270 deselected in 32.12s"), true);
	assert.equal(hasPytestSummary("3 passed in 0.5s"), true);
	assert.equal(hasPytestSummary("FAILED tests/test_a.py - assert 1 == 2"), false);
	assert.equal(hasPytestSummary("Found 1 error."), false);
	assert.equal(hasPytestSummary("passed"), false);
	assert.equal(hasPytestSummary("3 passed\nin 0.5s"), false);
});

test("DETECTED_SENTENCE carries the same fence bounds.ts uses, so a model reading a result cannot mistake either note for command output", () => {
	assert.equal(fenceNote(DETECTED_SENTENCE), `\n\n${NOTE_MARKER}\n${DETECTED_SENTENCE}`);
});

test("a result already carrying bounds.ts's fenced note (as it would once both extensions are live) gets exactly one more note appended, never a second copy of either", async () => {
	const pi = fakePi();
	registerRunner(pi.api, context(), async () => success({ exit_code: 0, output: "4 passed in 0.1s\n" }));
	const [onResult] = pi.handlers.tool_result;
	await onResult(SOURCE_EDIT);
	const boundsNote = fenceNote("Commands here are bounded at 120 seconds; on timeout Pi kills the process group.");
	const patch = await onResult({
		toolCallId: "b1", toolName: "bash", input: { command: "uv run pytest -q" },
		isError: false, content: [{ type: "text", text: `3 passed in 0.5s${boundsNote}` }], details: undefined,
	});
	const text = patch.content[0].text;
	// Both notes present, each exactly once, bounds' note first (it was
	// already in the incoming event) and the detection note appended after.
	assert.equal(text.split(NOTE_MARKER).length - 1, 2, `expected exactly two markers in ${JSON.stringify(text)}`);
	assert.ok(text.indexOf("Commands here are bounded") < text.indexOf(DETECTED_SENTENCE));
});

test("a bash result carrying a pytest summary after a landed source edit runs self_test once, records it, and appends the compact result", async () => {
	const pi = fakePi();
	const requests = [];
	registerRunner(pi.api, context(), async (request) => {
		requests.push(JSON.parse(request));
		return success({ exit_code: 0, output: "4 passed in 0.1s\n" });
	});
	const [onResult] = pi.handlers.tool_result;
	assert.equal(await onResult(SOURCE_EDIT), undefined);
	const patch = await onResult({
		toolCallId: "b1", toolName: "bash", input: { command: "uv run pytest -q" },
		isError: false, content: [{ type: "text", text: "3 passed in 0.5s" }], details: undefined,
	});
	assert.deepEqual(patch, {
		content: [{ type: "text", text: `3 passed in 0.5s${fenceNote(DETECTED_SENTENCE)}\nTest command exited 0\n4 passed in 0.1s\n` }],
		isError: false,
	});
	assert.equal(requests.length, 1);
	assert.equal(requests[0].operation, "test");
	assert.deepEqual(pi.entries, [
		{ kind: "finish_nudged", data: { generation: 1 } },
		{ kind: "self_test_detected", data: { generation: 1 } },
	]);
	assert.equal(pi.sent.length, 1);
	assert.equal(pi.sent[0].message.customType, "finish_nudged");
	// Consumed once: a second summary with no new mutation does not run again.
	assert.equal(await onResult({ toolCallId: "b2", toolName: "bash", isError: false,
		content: [{ type: "text", text: "3 passed in 0.5s" }], details: undefined }), undefined);
	assert.equal(requests.length, 1);
});

test("a summary with no pending mutation runs nothing, and a bash result without a summary is untouched", async () => {
	const pi = fakePi();
	let exchanges = 0;
	registerRunner(pi.api, context(), async () => {
		exchanges += 1;
		return success({ exit_code: 0, output: "4 passed in 0.1s\n" });
	});
	const [onResult] = pi.handlers.tool_result;
	// A completed self-test with no mutation leaves `checked === generation` (0).
	await pi.tool.execute("s1", {});
	assert.equal(await onResult({ toolCallId: "b1", toolName: "bash", isError: false,
		content: [{ type: "text", text: "3 passed in 0.5s" }], details: undefined }), undefined);
	assert.equal(exchanges, 1);
	// A landed source edit re-arms detection, but an output without a summary
	// is left exactly as it was.
	await onResult(SOURCE_EDIT);
	assert.equal(await onResult({ toolCallId: "b2", toolName: "bash", isError: false,
		content: [{ type: "text", text: "FAILED tests/test_a.py - assert 1 == 2" }], details: undefined }), undefined);
	assert.equal(exchanges, 1);
});

test("a fresh session with no mutation does not detect", async () => {
	// Plan Task 6: output without a mutation generation must not fire. `checked`
	// starts null, so the generation-0 guard, not the checked comparison, is
	// what stops this.
	const pi = fakePi();
	let exchanges = 0;
	registerRunner(pi.api, context(), async () => {
		exchanges += 1;
		return success({ exit_code: 0, output: "4 passed in 0.1s\n" });
	});
	const [onResult] = pi.handlers.tool_result;
	assert.equal(await onResult({ toolCallId: "b1", toolName: "bash", isError: false,
		content: [{ type: "text", text: "3 passed in 0.5s" }], details: undefined }), undefined);
	assert.equal(exchanges, 0);
	assert.deepEqual(pi.entries, []);
});

test("a detected run the engine refuses is an error result and is recorded", async () => {
	const pi = fakePi();
	registerRunner(pi.api, context(), async () => (
		{ version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "gone", result: null }
	));
	const [onResult] = pi.handlers.tool_result;
	await onResult(SOURCE_EDIT);
	const refused = await onResult({ toolCallId: "b1", toolName: "bash", isError: false,
		content: [{ type: "text", text: "3 passed in 0.5s" }], details: undefined });
	assert.equal(refused.isError, true);
	assert.match(refused.content[0].text, /TEST_COMMAND_UNAVAILABLE: gone$/);
	assert.deepEqual(pi.entries, [{ kind: "self_test_detected", data: { generation: 1 } }]);
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

test("a detected bash test run satisfies the gate; a successful write re-arms it; a refused write does not", async () => {
	const gate = gated([success({ exit_code: 0, output: "3 passed" })]);
	await gate.result(SOURCE_EDIT);
	await gate.result({ toolCallId: "b1", toolName: "bash", isError: false,
		content: [{ type: "text", text: "3 passed in 0.5s" }], details: undefined });
	await gate.result({ toolCallId: "w0", toolName: "write", input: { path: "app.py", content: "x" }, isError: true, content: [], details: undefined });
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	await gate.result({ toolCallId: "w1", toolName: "write", input: { path: "app.py", content: "x" }, isError: false, content: [], details: undefined });
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries.map((entry) => entry.kind), ["finish_nudged", "self_test_detected", "self_test_enforced"]);
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

// Red-stop gate (plan 2026-09-23, Revision 1 after Opus review): a final
// turn whose last completed self-test, at the current generation, did not
// pass gets a fresh re-run through the same exchange as the enforced
// branch, once, and a follow-up only if that re-run is still red. The
// re-run (not the stored result) is what the message and the recorded
// entry reflect.

test("redStopMessage carries the design's text over the (fresh) result", () => {
	assert.equal(
		redStopMessage("R"),
		"Before you finish: your last self_test did not pass, so the Engine ran it again on the current tree, and it still does not pass.\nR",
	);
});

test("fires: a red self_test the model ran, then a final turn with nothing further, re-runs and is a red stop", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED tests/test_app.py::test_home - assert 404 == 200" }),
		success({ exit_code: 1, output: "FAILED tests/test_app.py::test_home - assert 404 == 200" }),
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.turnEnd(FINAL);
	// One exchange for the model's own call, a second for the gate's re-run.
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_red_stop", data: { generation: 1, code: "OK", exit_code: 1, follow_up: true } },
	]);
	assert.deepEqual(gate.pi.sent, [
		{
			message: {
				customType: "self_test_red_stop",
				content: redStopMessage("Test command exited 1\nFAILED tests/test_app.py::test_home - assert 404 == 200"),
				display: true,
				details: undefined,
			},
			options: { deliverAs: "followUp" },
		},
	]);
});

test("sibling, green: a mutation and a green self_test after a red one leaves the red stop silent (no re-run, lastPassed is already true)", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED" }),
		success({ exit_code: 0, output: "3 passed" }),
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s2", {});
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, []);
	assert.deepEqual(gate.pi.sent, []);
});

test("bash-fix re-run: a red self_test, then a final turn whose re-run comes back green (a fix landed outside the tracked mutation tools) sends nothing but still records the check", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED" }),
		success({ exit_code: 0, output: "3 passed" }),
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_red_stop", data: { generation: 1, code: "OK", exit_code: 0, follow_up: false } },
	]);
	assert.deepEqual(gate.pi.sent, []);
});

test("refused re-run: a red self_test, then a final turn whose re-run the engine refuses sends nothing and nulls lastPassed", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED" }),
		{ version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "gone", result: null },
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_red_stop", data: { generation: 1, code: "TEST_COMMAND_UNAVAILABLE", exit_code: null, follow_up: false } },
	]);
	assert.deepEqual(gate.pi.sent, []);
	// A further final turn with no mutation does not re-fire: `lastPassed`
	// is null (refused), not `false`.
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.equal(gate.pi.entries.length, 1);
});

test("sibling, once: a red stop fires once per generation; a further final turn with no mutation sends nothing more", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED" }),
		success({ exit_code: 1, output: "FAILED again" }),
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.turnEnd(FINAL);
	assert.equal(gate.pi.sent.length, 1);
	assert.equal(gate.exchanges(), 2);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.equal(gate.pi.sent.length, 1);
});

test("sibling, enforced counts: the enforced follow-up counts as told once too, so a red stop right after stays silent", async () => {
	const gate = gated([success({ exit_code: 1, output: "FAILED" })]);
	await gate.result(LANDED_EDIT);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 1, code: "OK", exit_code: 1, follow_up: true } },
	]);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 1, code: "OK", exit_code: 1, follow_up: true } },
	]);
});

test("sibling, new mutation re-arms the enforced gate, not red-stop: a red self_test, a mutation, then a final turn runs the enforced branch", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED" }),
		success({ exit_code: 1, output: "FAILED again" }),
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.result(LANDED_EDIT);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 2, code: "OK", exit_code: 1, follow_up: true } },
	]);
});

test("refused-enforced stale case (Revision 1 regression): a red self_test at gen 1, a mutation to gen 2, a length-cut final turn whose enforced run is refused resumes as a runaway, not a red stop, and a later final turn stays silent", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED" }),
		{ version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "gone", result: null },
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.result(LANDED_EDIT);
	await gate.turnEnd({ role: "assistant", stopReason: "length", content: [{ type: "text", text: "..." }] });
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 2, code: "TEST_COMMAND_UNAVAILABLE", exit_code: null, follow_up: false } },
		{ kind: "runaway_resumed", data: { resume: 1, output_tokens: null } },
	]);
	// Before Revision 1, `checked` had already advanced to generation 2 here
	// while `lastPassed` still held generation 1's `false` -- the stale
	// combination this test pins. The model stops again with no new
	// mutation; the refusal nulled `lastPassed`, so no red stop fires with
	// generation 1's failure text.
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.equal(gate.pi.entries.length, 2);
	assert.deepEqual(gate.pi.sent.map((entry) => entry.message.customType), ["runaway_resumed"]);
});

test("detected route counts: a bash-detected red self_test then a final turn re-runs and is a red stop too", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED tests/test_app.py::test_home - assert 404 == 200" }),
		success({ exit_code: 1, output: "FAILED tests/test_app.py::test_home - assert 404 == 200" }),
	]);
	await gate.result(SOURCE_EDIT);
	await gate.result({
		toolCallId: "b1",
		toolName: "bash",
		isError: false,
		content: [{ type: "text", text: "1 failed, 3 passed in 0.2s" }],
		details: undefined,
	});
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries.map((entry) => entry.kind), ["self_test_detected", "self_test_red_stop"]);
});

test("length-cut red stop: a stopReason: length final turn with a red last run re-runs, is a red stop, not a runaway resume", async () => {
	const gate = gated([
		success({ exit_code: 1, output: "FAILED" }),
		success({ exit_code: 1, output: "FAILED again" }),
	]);
	await gate.result(LANDED_EDIT);
	await gate.pi.tool.execute("s1", {});
	await gate.turnEnd({ role: "assistant", stopReason: "length", content: [{ type: "text", text: "..." }] });
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_red_stop", data: { generation: 1, code: "OK", exit_code: 1, follow_up: true } },
	]);
	assert.equal(gate.pi.sent.length, 1);
	assert.equal(gate.pi.sent[0].message.customType, "self_test_red_stop");
});

test("refused exchange: a self_test call the engine refuses leaves the red-stop state untouched; behaviour is unchanged from today", async () => {
	const gate = gated([
		{ version: 1, ok: false, code: "TEST_COMMAND_UNAVAILABLE", message: "gone", result: null },
		success({ exit_code: 1, output: "FAILED" }),
	]);
	await gate.result(LANDED_EDIT);
	const refused = await gate.pi.tool.execute("s1", {});
	assert.equal(refused.details.ok, false);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 2);
	assert.deepEqual(gate.pi.entries, [
		{ kind: "self_test_enforced", data: { generation: 1, code: "OK", exit_code: 1, follow_up: true } },
	]);
});

test("guard: the enforced branch running this turn is the only self_test exchange -- the red-stop branch does not also run", async () => {
	// Regression for a reviewer finding: the `!enforcedRan` guard on the
	// red-stop branch was not pinned by any test -- deleting it left all
	// existing tests passing. This asserts the observable invariant the
	// guard exists to protect: when the enforced branch runs this turn,
	// exactly one self_test exchange happens, never two. (Note: under
	// Revision 1's conditions, this same invariant also holds with the
	// guard removed, because a failing enforced run sets `redStopped = at`
	// before the red-stop branch's own condition is evaluated, and a
	// refused or passing enforced run leaves `lastPassed` unable to satisfy
	// `=== false`. The guard is still kept because the design names it
	// explicitly and a future change to that ordering should not have to
	// re-derive this proof; see the report for the full argument.)
	const gate = gated([success({ exit_code: 1, output: "FAILED" })]);
	await gate.result(LANDED_EDIT);
	await gate.turnEnd(FINAL);
	assert.equal(gate.exchanges(), 1);
	assert.deepEqual(gate.pi.entries.map((entry) => entry.kind), ["self_test_enforced"]);
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

test("a test path is any test module, conftest, or anything under tests/", () => {
	assert.equal(isTestPath("tests/test_app.py"), true);
	assert.equal(isTestPath("tests/conftest.py"), true);
	assert.equal(isTestPath("src/pkg/app_test.py"), true);
	assert.equal(isTestPath("tests/helpers/data.json"), true);
	assert.equal(isTestPath("src/pkg/app.py"), false);
	assert.equal(isTestPath("contests/app.py"), false);
});

test("isTestPath requires the .py extension on the basename patterns (Ruling A)", () => {
	assert.equal(isTestPath("conftest.py"), true);
	assert.equal(isTestPath("src/pkg/conftest.py"), true);
	assert.equal(isTestPath("a/tests/b/c.py"), true);
	assert.equal(isTestPath("src/testing/thing.py"), false);
	assert.equal(isTestPath("src/mytests/x.py"), false);
	assert.equal(isTestPath("test_thing.txt"), false);
});

test("the steer text is the design's paragraph, byte for byte, and names no path", () => {
	assert.equal(
		FINISH_STEER,
		"self_test passes on the current tree. If the requested change is complete, stop now and " +
			"report what you changed. Do not commit, add provenance rows, run the full repository suite, " +
			"run linters, type checkers or `just` recipes, remove unused code, tidy or refactor, do a " +
			"final check, or change your tests to match a count; the developer reviews the candidate and " +
			"does those. Editing a passing tree can only break it. If something in the request is still " +
			"missing, say which part and continue.",
	);
	assert.equal(FINISH_STEER.includes(".py"), false);
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

test("a length-cut tool-free assistant turn is a runaway", () => {
	assert.equal(isLengthCut({ role: "assistant", stopReason: "length", content: [{ type: "text", text: "..." }] }), true);
	assert.equal(isLengthCut({ role: "assistant", stopReason: "stop", content: [{ type: "text", text: "..." }] }), false);
	assert.equal(isLengthCut({ role: "assistant", stopReason: "length", content: [{ type: "toolCall", id: "t", name: "edit" }] }), false);
	assert.equal(isLengthCut({ role: "user", stopReason: "length", content: [] }), false);
});

test("the resume text is the design's paragraph, byte for byte, and names no path", () => {
	// Ruling 1 (plan): the resume text is the spec's, byte for byte -- a
	// literal-string pin, not a regex or a substring check, so a reworded
	// message (even one that keeps the key phrases) fails this test.
	assert.equal(
		RESUME_MESSAGE,
		"Your last turn hit the per-turn output cap with no tool call, so nothing was done. Do not " +
			"restate the plan. Make the next concrete change with a tool call: read the one file you " +
			"need, or edit.",
	);
	assert.equal(RESUME_MESSAGE.includes(".py"), false);
	assert.equal(/\//.test(RESUME_MESSAGE), false);
	assert.equal(MAX_RESUMES, 2);
});
