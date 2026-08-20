import assert from "node:assert/strict";
import test from "node:test";

import registerLoopBreaker, {
	THRESHOLD,
	WINDOW,
	createLoopBreaker,
} from "../packages/engine/engine.ts";

const repeated = (toolName = "bash", input = { command: "ls -R" }) => ({
	toolName,
	input,
});

function admit(breaker, call, count) {
	for (let index = 0; index < count; index += 1) {
		assert.equal(breaker.inspect(call), undefined);
	}
}

function registeredExtension({ appendEntry = () => undefined } = {}) {
	let handler;
	registerLoopBreaker({
		on(event, candidate) {
			assert.equal(event, "tool_call");
			handler = candidate;
		},
		appendEntry,
	});
	assert.equal(typeof handler, "function");
	return handler;
}

test("the sixth identical admitted call is refused with typed telemetry", () => {
	const breaker = createLoopBreaker();
	const call = repeated();
	admit(breaker, call, THRESHOLD);

	assert.deepEqual(breaker.inspect(call), {
		block: true,
		reason:
			"This exact bash call already appeared 5 times in the last 20 admitted tool calls. " +
			"Running it again will not change the result. Use what you already know and take a different concrete action.",
		entry: {
			kind: "loop_broken",
			data: { tool: "bash", repeats: 5, blockedSoFar: 1 },
		},
	});
});

test("a varied sixth call is admitted", () => {
	const breaker = createLoopBreaker();
	admit(breaker, repeated(), THRESHOLD);

	assert.equal(breaker.inspect(repeated("bash", { command: "find . -maxdepth 2" })), undefined);
});

test("object key order is ignored recursively", () => {
	const breaker = createLoopBreaker();
	admit(
		breaker,
		repeated("write", { path: "result.json", value: { alpha: 1, beta: { x: true, y: null } } }),
		THRESHOLD,
	);

	assert.equal(
		breaker.inspect(
			repeated("write", { value: { beta: { y: null, x: true }, alpha: 1 }, path: "result.json" }),
		)?.block,
		true,
	);
});

test("array order and tool name remain significant", () => {
	const breaker = createLoopBreaker();
	admit(breaker, repeated("write", { values: [1, 2] }), THRESHOLD);

	assert.equal(breaker.inspect(repeated("write", { values: [2, 1] })), undefined);
	assert.equal(breaker.inspect(repeated("edit", { values: [1, 2] })), undefined);
});

test("twenty newer admitted calls evict an older key", () => {
	const breaker = createLoopBreaker();
	const target = repeated("read", { path: "old.py" });
	admit(breaker, target, THRESHOLD);
	for (let index = 0; index < WINDOW; index += 1) {
		assert.equal(breaker.inspect(repeated("read", { path: `new-${index}.py` })), undefined);
	}

	assert.equal(breaker.inspect(target), undefined);
});

test("blocked calls never enter the admitted window", () => {
	const breaker = createLoopBreaker();
	const call = repeated("ls", { path: "." });
	admit(breaker, call, THRESHOLD);

	assert.equal(breaker.inspect(call)?.entry.data.blockedSoFar, 1);
	assert.equal(breaker.inspect(call)?.entry.data.blockedSoFar, 2);
	assert.equal(breaker.inspect(call)?.entry.data.repeats, THRESHOLD);
});

test("unsupported and cyclic inputs are admitted without changing state", () => {
	const breaker = createLoopBreaker();
	const cyclic = {};
	cyclic.self = cyclic;

	for (let index = 0; index < THRESHOLD + 1; index += 1) {
		assert.equal(breaker.inspect(repeated("write", cyclic)), undefined);
		assert.equal(breaker.inspect(repeated("write", { value: 1n })), undefined);
	}
});

test("JSON primitives and plain container variants are accepted", () => {
	const breaker = createLoopBreaker();
	const dictionary = Object.create(null);
	dictionary.value = false;
	for (const input of [null, true, false, 0, -0, 1.5, "text", [], {}, dictionary]) {
		assert.equal(breaker.inspect(repeated("write", input)), undefined);
	}
});

test("every non-JSON value is admitted without entering the window", () => {
	for (const input of [
		undefined,
		Symbol("value"),
		() => undefined,
		Number.NaN,
		Number.POSITIVE_INFINITY,
		new Date(0),
		[1n],
	]) {
		const breaker = createLoopBreaker();
		for (let index = 0; index < THRESHOLD + 1; index += 1) {
			assert.equal(breaker.inspect({ toolName: "write", input }), undefined);
		}
	}
});

test("each extension registration owns an empty breaker", async () => {
	const call = repeated("bash", { command: "registration-isolation" });
	const first = registeredExtension();
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await first(call), undefined);
	}

	const second = registeredExtension();
	assert.equal(await second(call), undefined);
	assert.equal((await first(call))?.block, true);
});

test("the Pi adapter appends one entry and returns only Pi's block shape", async () => {
	const entries = [];
	const handler = registeredExtension({
		appendEntry(kind, data) {
			entries.push({ kind, data });
		},
	});
	const call = repeated("bash", { command: "adapter-telemetry" });
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await handler(call), undefined);
	}

	const decision = await handler(call);
	assert.deepEqual(decision, {
		block: true,
		reason:
			"This exact bash call already appeared 5 times in the last 20 admitted tool calls. " +
			"Running it again will not change the result. Use what you already know and take a different concrete action.",
	});
	assert.deepEqual(entries, [
		{
			kind: "loop_broken",
			data: { tool: "bash", repeats: 5, blockedSoFar: 1 },
		},
	]);
});

test("telemetry failure cannot escape or admit an already blocked call", async () => {
	const handler = registeredExtension({
		appendEntry() {
			throw new Error("telemetry unavailable");
		},
	});
	const call = repeated("bash", { command: "telemetry-failure" });
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await handler(call), undefined);
	}

	assert.equal((await handler(call))?.block, true);
});

test("unexpected canonicalization errors cannot escape the Pi handler", async () => {
	const handler = registeredExtension();
	const throwingInput = new Proxy(
		{},
		{
			ownKeys() {
				throw new Error("cannot enumerate");
			},
		},
	);

	assert.equal(await handler(repeated("write", throwingInput)), undefined);
});

test("unexpected Pi event access errors cannot escape the handler", async () => {
	const handler = registeredExtension();
	const throwingEvent = new Proxy(
		{},
		{
			get() {
				throw new Error("cannot read event");
			},
		},
	);

	assert.equal(await handler(throwingEvent), undefined);
});
