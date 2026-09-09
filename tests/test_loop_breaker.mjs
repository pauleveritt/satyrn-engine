import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import registerLoopBreaker, {
	THRESHOLD,
	CONSECUTIVE_BLOCK_LIMIT,
	WINDOW,
	createLoopBreaker,
	requireProgressEnabled,
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

/** The digest `mutator.ts` would report for a named state of a file. */
const stateSha = (state) => createHash("sha256").update(String(state)).digest("hex");

/** A Pi `tool_result` event for the bounded editor. */
const editResult = (details) => ({ toolName: "edit", details });

/** A landed edit leaving `path` holding the bytes digested by `stateSha(state)`. */
const landedEdit = (path, state) =>
	editResult({
		satyrn: true,
		ok: true,
		code: "OK",
		result: { path, sha256: stateSha(state), region: "  1 | changed" },
	});

const refusedEdit = (code) => editResult({ satyrn: true, ok: false, code, result: null });

function registeredExtension({ appendEntry = () => undefined } = {}) {
	const handlers = new Map();
	registerLoopBreaker({
		on(event, candidate) {
			assert.ok(
				event === "tool_call" || event === "tool_result",
				`unexpected registered event ${event}`,
			);
			assert.equal(handlers.has(event), false, `event ${event} registered twice`);
			handlers.set(event, candidate);
		},
		appendEntry,
	});
	assert.deepEqual([...handlers.keys()].sort(), ["tool_call", "tool_result"]);
	assert.equal(typeof handlers.get("tool_call"), "function");
	assert.equal(typeof handlers.get("tool_result"), "function");
	return { call: handlers.get("tool_call"), result: handlers.get("tool_result") };
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

test("a top-level __proto__ key is canonicalized as JSON data", () => {
	const breaker = createLoopBreaker();
	const target = repeated(
		"write",
		JSON.parse('{"__proto__":{"sentinel":"target"},"path":"result.json"}'),
	);
	const sibling = repeated(
		"write",
		JSON.parse('{"__proto__":{"sentinel":"sibling"},"path":"result.json"}'),
	);
	admit(breaker, target, THRESHOLD);

	assert.equal(breaker.inspect(sibling), undefined);
	assert.equal(breaker.inspect(target)?.block, true);
});

test("a nested __proto__ key is canonicalized as JSON data", () => {
	const breaker = createLoopBreaker();
	const target = repeated(
		"write",
		JSON.parse('{"value":{"__proto__":{"sentinel":"target"}}}'),
	);
	const sibling = repeated(
		"write",
		JSON.parse('{"value":{"__proto__":{"sentinel":"sibling"}}}'),
	);
	admit(breaker, target, THRESHOLD);

	assert.equal(breaker.inspect(sibling), undefined);
	assert.equal(breaker.inspect(target)?.block, true);
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
	assert.equal(breaker.inspect(target)?.entry.data.blockedSoFar, 1);
	assert.equal(breaker.inspect(target)?.entry.data.blockedSoFar, 2);
	for (let index = 0; index < WINDOW; index += 1) {
		assert.equal(breaker.inspect(repeated("read", { path: `new-${index}.py` })), undefined);
	}

	assert.equal(breaker.inspect(target), undefined);
	admit(breaker, target, THRESHOLD - 1);
	assert.equal(breaker.inspect(target)?.entry.data.blockedSoFar, 1);
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
	const { call: first } = registeredExtension();
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await first(call), undefined);
	}

	const { call: second } = registeredExtension();
	assert.equal(await second(call), undefined);
	assert.equal((await first(call))?.block, true);
});

test("the Pi adapter appends one entry and returns only Pi's block shape", async () => {
	const entries = [];
	const { call: handler } = registeredExtension({
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

test("the third consecutive blocked call terminates the print-mode turn", async () => {
	const entries = [];
	const { call: handler } = registeredExtension({
		appendEntry(kind, data) {
			entries.push({ kind, data });
		},
	});
	const call = repeated("read", { path: "tests/test_app.py" });
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await handler(call), undefined);
	}

	assert.deepEqual(await handler(call), {
		block: true,
		reason:
			"This exact read call already appeared 5 times in the last 20 admitted tool calls. " +
			"Running it again will not change the result. Use what you already know and take a different concrete action.",
	});
	assert.deepEqual(await handler(call), {
		block: true,
		reason:
			"This exact read call already appeared 5 times in the last 20 admitted tool calls. " +
			"Running it again will not change the result. Use what you already know and take a different concrete action.",
	});
	assert.deepEqual(await handler(call), {
		block: true,
		reason:
			"This exact read call already appeared 5 times in the last 20 admitted tool calls. " +
			"Running it again will not change the result. Use what you already know and take a different concrete action.",
		terminate: true,
	});
	assert.equal(entries.length, CONSECUTIVE_BLOCK_LIMIT);
});

test("an admitted call resets the consecutive blocked-call termination count", async () => {
	const { call: handler } = registeredExtension();
	const blocked = repeated("read", { path: "tests/test_app.py" });
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await handler(blocked), undefined);
	}

	assert.equal((await handler(blocked))?.terminate, undefined);
	assert.equal((await handler(blocked))?.terminate, undefined);
	assert.equal(await handler(repeated("read", { path: "app.py" })), undefined);
	assert.equal((await handler(blocked))?.terminate, undefined);
	assert.equal((await handler(blocked))?.terminate, undefined);
	assert.equal((await handler(blocked))?.terminate, true);
});

test("a fail-open inspection error resets the consecutive blocked-call termination count", async () => {
	const { call: handler } = registeredExtension();
	const blocked = repeated("read", { path: "tests/test_app.py" });
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await handler(blocked), undefined);
	}
	assert.equal((await handler(blocked))?.terminate, undefined);
	assert.equal((await handler(blocked))?.terminate, undefined);

	const throwingEvent = new Proxy(
		{},
		{
			get() {
				throw new Error("cannot read event");
			},
		},
	);
	assert.equal(await handler(throwingEvent), undefined);
	assert.equal((await handler(blocked))?.terminate, undefined);
	assert.equal((await handler(blocked))?.terminate, undefined);
	assert.equal((await handler(blocked))?.terminate, true);
});

test("telemetry failure cannot escape or admit an already blocked call", async () => {
	const { call: handler } = registeredExtension({
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
	const { call: handler } = registeredExtension();
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
	const { call: handler } = registeredExtension();
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

test("forward repair: reading and retesting after each new state is never refused", async () => {
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });
	const retest = repeated("bash", { command: "pytest -q tests/test_app.py" });

	// One repair cycle: look at the file, change it, look again, retest.
	// Every landed edit here leaves app.py in bytes it has never held before
	// in this attempt, so it makes the previous read and the previous test
	// run stale: repeating them is verification, not cycling.
	for (let cycle = 0; cycle < THRESHOLD + 1; cycle += 1) {
		assert.equal(await call(read), undefined, `read refused in cycle ${cycle + 1}`);
		assert.equal(
			await call(
				repeated("edit", {
					path: "app.py",
					edits: [{ oldText: `anchor ${cycle}`, newText: `replacement ${cycle}` }],
				}),
			),
			undefined,
			`edit refused in cycle ${cycle + 1}`,
		);
		assert.equal(await result(landedEdit("app.py", cycle)), undefined);
		assert.equal(await call(retest), undefined, `retest refused in cycle ${cycle + 1}`);
	}
});

test("a refused edit changes nothing, so identical calls around it stay bounded", async () => {
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });

	for (const code of ["NO_CHANGE_REQUESTED", "ANCHOR_ALREADY_APPLIED", "ANCHOR_MISSING"]) {
		assert.equal(await call(read), undefined);
		assert.equal(await result(refusedEdit(code)), undefined);
	}
	assert.equal(await call(read), undefined);
	assert.equal(await call(read), undefined);

	assert.equal((await call(read))?.block, true);
});

test("a result from another tool is not evidence that the workspace changed", async () => {
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });
	const foreign = [
		{ toolName: "bash", details: { satyrn: true, ok: true, code: "OK", result: null } },
		{ toolName: "edit", details: undefined },
		{ toolName: "edit", details: null },
		{ toolName: "edit", details: { ok: true } },
	];

	for (const [index, event] of foreign.entries()) {
		assert.equal(await call(read), undefined, `read refused at result ${index}`);
		assert.equal(await result(event), undefined);
	}
	assert.equal(await call(read), undefined);

	assert.equal((await call(read))?.block, true);
});

test("an unreadable tool_result cannot escape the handler or retire the window", async () => {
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });
	const throwingEvent = new Proxy(
		{},
		{
			get() {
				throw new Error("cannot read result");
			},
		},
	);

	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await call(read), undefined);
		assert.equal(await result(throwingEvent), undefined);
	}

	assert.equal((await call(read))?.block, true);
});

test("a landed edit retires the window without loosening the threshold for what follows", async () => {
	const { call, result } = registeredExtension();
	const retest = repeated("bash", { command: "pytest -q" });

	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await call(retest), undefined);
	}
	assert.equal((await call(retest))?.block, true);

	assert.equal(await result(landedEdit("app.py", "new")), undefined);
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await call(retest), undefined, `retest refused after a landed edit`);
	}

	assert.deepEqual((await call(retest))?.entry, undefined);
	assert.equal((await call(retest))?.block, true);
});

test("a landed edit resets blockedSoFar with the window it belongs to", () => {
	const breaker = createLoopBreaker();
	const call = repeated("read", { path: "app.py" });
	admit(breaker, call, THRESHOLD);
	assert.equal(breaker.inspect(call)?.entry.data.blockedSoFar, 1);
	assert.equal(breaker.inspect(call)?.entry.data.blockedSoFar, 2);

	breaker.noteChange("app.py", stateSha("new"));

	admit(breaker, call, THRESHOLD);
	assert.equal(breaker.inspect(call)?.entry.data.blockedSoFar, 1);
});

test("consecutive blocks still terminate the turn when no edit lands", async () => {
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });
	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await call(read), undefined);
	}

	assert.equal((await call(read))?.terminate, undefined);
	assert.equal(await result(refusedEdit("ANCHOR_MISSING")), undefined);
	assert.equal((await call(read))?.terminate, undefined);
	assert.equal((await call(read))?.terminate, true);
});

test("churn: alternating edits between two states is still refused, and terminates the turn", async () => {
	// The v14a cell-009 shape (retained batch 2026-09-07-v14a-123039): the
	// model toggled `"complaint_model": Complaint` into and out of app.py
	// nine times, re-reading between every toggle. Every toggle is a real,
	// unique, not-yet-applied anchor, so every edit LANDS -- landing alone
	// therefore cannot be the progress signal. Because the read is keyed on
	// app.py's revision, a toggle back to a revision the file has already
	// held reproduces an earlier key, so those keys RECUR and accumulate to
	// THRESHOLD exactly as an unchanging workspace would.
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });

	assert.equal(await result(landedEdit("app.py", "with-model")), undefined);

	// Ten reads across ten toggles: five at each of the two revisions.
	for (let toggle = 0; toggle < 2 * THRESHOLD; toggle += 1) {
		assert.equal(await call(read), undefined, `read refused too early at toggle ${toggle}`);
		const state = toggle % 2 === 0 ? "without-model" : "with-model";
		assert.equal(await result(landedEdit("app.py", state)), undefined);
	}

	assert.equal((await call(read))?.block, true);
	assert.equal((await call(read))?.terminate, undefined);
	assert.equal((await call(read))?.terminate, true);
});

test("editing one path does not unblock repeated reads of an unchanged other", async () => {
	// The regression this replaces: retirement used to be global, so a landed
	// edit to app.py also retired repeats of reads of an untouched models.py.
	// models.py's revision never moves, so its read key never moves either.
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "models.py" });

	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await call(read), undefined, `read refused too early at edit ${index}`);
		assert.equal(await result(landedEdit("app.py", `app-${index}`)), undefined);
	}

	assert.equal((await call(read))?.block, true);
	assert.equal((await call(read))?.terminate, undefined);
	assert.equal((await call(read))?.terminate, true);
});

test("forty reads of an unchanged path stay bounded while another path keeps changing", async () => {
	// The measured shape: forty reads of an unchanged models.py, a novel edit
	// to app.py landing between every one of them.
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "models.py" });
	let admitted = 0;
	let blocked = 0;

	for (let index = 0; index < 40; index += 1) {
		if ((await call(read)) === undefined) admitted += 1;
		else blocked += 1;
		assert.equal(await result(landedEdit("app.py", `app-${index}`)), undefined);
	}

	assert.deepEqual({ admitted, blocked }, { admitted: THRESHOLD, blocked: 40 - THRESHOLD });
});

test("a test command at a workspace the tree has already held reproduces its key", async () => {
	// A path-less call carries the whole revision map, so it goes free after a
	// landed edit anywhere. Churn across two files still returns the map to a
	// pair it has already held, so the key recurs and accumulates to THRESHOLD.
	const { call, result } = registeredExtension();
	const retest = repeated("bash", { command: "pytest -q" });

	assert.equal(await result(landedEdit("b.py", "b-1")), undefined);
	for (let toggle = 0; toggle < THRESHOLD; toggle += 1) {
		assert.equal(await result(landedEdit("a.py", "a-1")), undefined);
		assert.equal(await call(retest), undefined, `test command refused too early at ${toggle}`);
		assert.equal(await result(landedEdit("a.py", "a-2")), undefined);
		assert.equal(await call(retest), undefined);
	}

	assert.equal(await result(landedEdit("a.py", "a-1")), undefined);
	assert.equal((await call(retest))?.block, true);
});

test("editing a path permits reading it back and re-running the test command", async () => {
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });
	const retest = repeated("bash", { command: "pytest -q tests/test_app.py" });

	for (let index = 0; index < THRESHOLD; index += 1) {
		assert.equal(await call(read), undefined);
		assert.equal(await call(retest), undefined);
	}
	assert.equal((await call(read))?.block, true);
	assert.equal((await call(retest))?.block, true);

	// One real change to app.py: the read of app.py carries app.py's new
	// revision, and the path-less test command carries the new revision map.
	assert.equal(await result(landedEdit("app.py", "repaired")), undefined);

	assert.equal(await call(read), undefined);
	assert.equal(await call(retest), undefined);

	// An edit to a second file frees the whole-tree command again: it can
	// change what the test run prints. The read of app.py keeps counting under
	// app.py's own revision, which that edit did not move.
	assert.equal(await result(landedEdit("zz.py", "added")), undefined);
	assert.equal(await call(retest), undefined);
	assert.equal(await call(read), undefined);
});

test("a landed edit whose evidence cannot be read does not clear staleness", async () => {
	const { call, result } = registeredExtension();
	const read = repeated("read", { path: "app.py" });
	const unreadable = [
		editResult({ satyrn: true, ok: true, code: "OK", result: null }),
		editResult({
			satyrn: true,
			ok: true,
			code: "OK",
			result: { path: "app.py", region: "  1 | changed" },
		}),
		editResult({
			satyrn: true,
			ok: true,
			code: "OK",
			result: { path: "app.py", sha256: 5, region: "  1 | changed" },
		}),
		editResult({
			satyrn: true,
			ok: true,
			code: "OK",
			result: { path: "app.py", sha256: "not-a-digest", region: "  1 | changed" },
		}),
		editResult({
			satyrn: true,
			ok: true,
			code: "OK",
			result: { path: 7, sha256: stateSha("f"), region: "  1 | changed" },
		}),
	];

	for (const [index, event] of unreadable.entries()) {
		assert.equal(await call(read), undefined, `read refused at result ${index}`);
		assert.equal(await result(event), undefined);
	}

	assert.equal((await call(read))?.block, true);
});

test("a repeated state reported straight to the breaker clears nothing", () => {
	const breaker = createLoopBreaker();
	const call = repeated("read", { path: "app.py" });
	breaker.noteChange("app.py", stateSha("first"));
	admit(breaker, call, THRESHOLD);

	breaker.noteChange("app.py", stateSha("first"));

	assert.equal(breaker.inspect(call)?.block, true);
});



// --- 2026-09-08: a cycle that measured a breaker this file no longer has ---
//
// Recorded because the measurement is worth keeping and its conclusions were
// wrong, and a later reader should meet both together.
//
// A mining cycle over 236 retained Engine-cell transcripts found the loop
// breaker's refusal firing 532 times, with the model's next call identical in
// 174 of them (33%), and one call key blocked 30 times in a single cell.
//
// **Those numbers describe the breaker at `25ca0be` (engine.ts digest
// 2e4fc064…), which every mined batch ran, and which this file no longer
// contains.** `fc22622` keyed the workspace revision into a call afterwards --
// and 195 of those 532 refusals (37%) were stale under the old rule: an
// accepted edit had landed since the last identical call, so "Running it again
// will not change the result" was FALSE when it was said. Revision keying is
// what addresses that. Whether the shipping breaker still issues those
// refusals at all is unmeasured; the transcripts predate it.
//
// Two further corrections to that cycle, kept so they are not re-derived:
//
// 1. A remedy was written -- terminate on the per-key `blockedSoFar` the
//    breaker already tracks, instead of only on consecutive blocks -- and it
//    was rejected on the ground that "of the 49 cells blocking one key more
//    than three times, 27 passed, so it would have terminated 27 successful
//    attempts". **That overstates by about 9x.** 42 of the 49 were terminated
//    later anyway by the consecutive rule, and of the 27 passes, 24 landed no
//    accepted edit after the fourth block -- their retained patch would have
//    been byte-identical. At most 3 could have differed. The remedy is still
//    not adopted, but on the narrower ground that it is aimed at a breaker
//    that has since changed.
// 2. "One different call between re-sends keeps the turn alive indefinitely"
//    is **false**. The interleaved call accumulates toward THRESHOLD too, and
//    a two-key alternation terminates. What sustains a turn is interleaving
//    *novel* calls, which the comment at the head of this module already says
//    no state-keyed rule closes.
//
// And the mechanism the cycle missed: a blocked key is re-admitted once the
// window evicts it, so `blockedSoFar` reaching 30 is accumulated across
// block-and-readmit cycles rather than 30 refusals of a permanently barred
// call -- the identical re-send the message forbids does eventually work.
// That is a better explanation of the 33% than anything about wording.

test("a repeatedly refused call does not terminate a turn that keeps progressing", () => {
	// The behaviour the refuted remedy would have changed, pinned as it is.
	// The remedy was: in registerLoopBreaker, terminate when
	// decision.entry.data.blockedSoFar >= CONSECUTIVE_BLOCK_LIMIT as well as on
	// consecutive blocks. Under it, `terminate` becomes true at the second
	// refusal below, so this row is what fails if it is ever reapplied.
	const { call: onToolCall } = registeredExtension();
	const looped = repeated("read", { path: "app.py" });
	const other = repeated("read", { path: "models.py" });
	const send = (c) => onToolCall({ toolName: c.toolName, input: c.input });

	return (async () => {
		for (let i = 0; i < THRESHOLD; i += 1) await send(looped);
		// Two rounds only: the interleaved call accumulates toward THRESHOLD as
		// well, so a longer alternation refuses `other` too and then terminates
		// -- which is why "indefinitely" above is withdrawn.
		for (let i = 0; i < 2; i += 1) {
			const decision = await send(looped);
			assert.equal(decision?.block, true, "the repeated call stays refused");
			assert.equal(
				decision?.terminate,
				undefined,
				"a turn still admitting other calls is refused, not terminated",
			);
			const interleaved = await send(other);
			assert.equal(interleaved, undefined, "the interleaved call is still admitted");
		}
	})();
});

// --- 2026-09-09: the opt-in progress rule, and its default ---
//
// Terminate on a refusal only when nothing has been accepted yet in the turn.
// Measured over 236 retained transcripts: a refusal arriving with no accepted
// edit sits in a cell whose eventual pass-rate is 0.09, against 0.74 and 0.42
// once edits have landed. Per cell the rule ends 38 of 236 -- 26 that produced
// no patch, 7 that failed, 5 that passed -- so it is a spending rule with a
// real cost, and it is OFF unless a batch asks for it.

test("with the progress rule off, a refusal without progress does not terminate", () => {
	// The default, and the row that fails if the rule is ever made default-on.
	const { call } = registeredExtension();
	const looped = repeated("read", { path: "app.py" });
	return (async () => {
		for (let i = 0; i < THRESHOLD; i += 1) await call({ toolName: looped.toolName, input: looped.input });
		const decision = await call({ toolName: looped.toolName, input: looped.input });
		assert.equal(decision?.block, true);
		assert.equal(decision?.terminate, undefined, "off by default");
	})();
});

test("requireProgressEnabled reads only the exact opt-in value", () => {
	assert.equal(requireProgressEnabled({ SATYRN_BREAKER_REQUIRE_PROGRESS: "1" }), true);
	// The refusal direction: anything else is off, so a stray or truthy-looking
	// value cannot silently enable a rule that ends turns.
	for (const value of ["0", "true", "yes", "", undefined]) {
		assert.equal(
			requireProgressEnabled({ SATYRN_BREAKER_REQUIRE_PROGRESS: value }),
			false,
			`value ${JSON.stringify(value)} must not enable the rule`,
		);
	}
	assert.equal(requireProgressEnabled({}), false);
});

test("with the progress rule on, a refusal before any accepted edit terminates", () => {
	// The rule's whole point: a refusal arriving when nothing has landed sits
	// in a cell whose measured pass-rate is 0.09.
	const previous = process.env.SATYRN_BREAKER_REQUIRE_PROGRESS;
	process.env.SATYRN_BREAKER_REQUIRE_PROGRESS = "1";
	try {
		const { call } = registeredExtension();
		const looped = repeated("read", { path: "app.py" });
		return (async () => {
			for (let i = 0; i < THRESHOLD; i += 1) {
				await call({ toolName: looped.toolName, input: looped.input });
			}
			const decision = await call({ toolName: looped.toolName, input: looped.input });
			assert.equal(decision?.block, true);
			assert.equal(decision?.terminate, true, "nothing has landed; end the turn");
		})();
	} finally {
		if (previous === undefined) delete process.env.SATYRN_BREAKER_REQUIRE_PROGRESS;
		else process.env.SATYRN_BREAKER_REQUIRE_PROGRESS = previous;
	}
});

test("with the progress rule on, an accepted edit spares the turn", () => {
	// The sibling that stops the rule being a disguised always-terminate: once
	// an edit has landed, a refusal is a refusal again. Without this row a rule
	// that ended every refused turn would pass the row above.
	const previous = process.env.SATYRN_BREAKER_REQUIRE_PROGRESS;
	process.env.SATYRN_BREAKER_REQUIRE_PROGRESS = "1";
	try {
		const { call, result } = registeredExtension();
		const looped = repeated("read", { path: "app.py" });
		return (async () => {
			await result(landedEdit("models.py", "one"));
			for (let i = 0; i < THRESHOLD; i += 1) {
				await call({ toolName: looped.toolName, input: looped.input });
			}
			const decision = await call({ toolName: looped.toolName, input: looped.input });
			assert.equal(decision?.block, true);
			assert.equal(decision?.terminate, undefined, "progress spares the turn");
		})();
	} finally {
		if (previous === undefined) delete process.env.SATYRN_BREAKER_REQUIRE_PROGRESS;
		else process.env.SATYRN_BREAKER_REQUIRE_PROGRESS = previous;
	}
});
