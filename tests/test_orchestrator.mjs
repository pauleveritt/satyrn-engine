/** Node tests for the E2 transport and E5 delivery adapter. */

import assert from "node:assert/strict";
import { resolve } from "node:path";
import { test } from "node:test";

import registerExtension, {
	AdapterRefusal,
	MAX_RECEIPT_BYTES,
	buildDeliveryInvocation,
	buildRequest,
	createAdapter,
	exchange,
	parseDeliveryReceipt,
	parseResponse,
	runDelivery,
} from "../packages/engine/orchestrator.ts";

const CHECK_OK = JSON.stringify({ version: 1, ok: true, code: "OK", message: "" });
const CHECK_REFUSAL = JSON.stringify({
	version: 1,
	ok: false,
	code: "CONTRACT_UNREADABLE",
	message: "missing",
});
const DELIVERY_OK = JSON.stringify({
	version: 1,
	outcome: "candidate-created",
	code: "OK",
	message: "candidate created",
	contract_id: "task",
	repository: "/repo",
	base_commit: "base",
	candidate_ref: "refs/satyrn/candidates/task/head",
	candidate_commit: "candidate",
	changed_paths: ["app.py"],
	command_exit: 0,
	worktree_path: null,
});
const DELIVERY_REFUSAL = JSON.stringify({
	version: 1,
	outcome: "refused",
	code: "REPO_DIRTY",
	message: "dirty",
	contract_id: "task",
	repository: "/repo",
	base_commit: "base",
	candidate_ref: null,
	candidate_commit: null,
	changed_paths: null,
	command_exit: null,
	worktree_path: null,
});

function child(options = {}) {
	const {
		stdout = "",
		stderr = "",
		exitCode = 0,
		never = false,
		error = null,
		writeError = null,
		asyncWriteError = null,
		endError = null,
		ignoreTerm = false,
	} = options;
	const listeners = { close: [], error: [] };
	const killSignals = [];
	const stdinListeners = { error: [] };
	let closed = false;
	const emitClose = () => {
		if (closed) return;
		closed = true;
		for (const callback of listeners.close) callback(exitCode);
	};
	const result = {
		stdin: {
			write() {
				if (writeError) throw writeError;
			},
			end() {
				if (endError) throw endError;
				if (asyncWriteError) {
					queueMicrotask(() => {
						for (const callback of stdinListeners.error) callback(asyncWriteError);
					});
				}
				queueMicrotask(() => {
					if (!never && !asyncWriteError) emitClose();
				});
			},
			on(event, callback) {
				stdinListeners[event].push(callback);
			},
		},
		stdout: {
			on(event, callback) {
				if (event === "data" && stdout) queueMicrotask(() => callback(stdout));
			},
		},
		stderr: {
			on(event, callback) {
				if (event === "data" && stderr) queueMicrotask(() => callback(stderr));
			},
		},
		on(event, callback) {
			listeners[event].push(callback);
		},
		kill(signal = "SIGTERM") {
			killSignals.push(signal);
			if (signal === "SIGKILL" || !ignoreTerm) queueMicrotask(emitClose);
		},
		get killed() {
			return killSignals.length > 0;
		},
		get killSignals() {
			return killSignals;
		},
		emit(event, value) {
			for (const callback of listeners[event]) callback(value);
		},
	};
	if (error) queueMicrotask(() => { for (const callback of listeners.error) callback(error); });
	return result;
}

function spawnerFor(value) {
	return () => value;
}

async function refusal(promise, code) {
	await assert.rejects(promise, (error) => error instanceof AdapterRefusal && error.code === code);
}

test("check request and response stay compatible", () => {
	assert.equal(
		buildRequest("/repo", "/contract"),
		'{"version":1,"operation":"check","repo":"/repo","contract":"/contract"}',
	);
	assert.deepEqual(parseResponse(CHECK_OK), { version: 1, ok: true, code: "OK", message: "" });
	assert.deepEqual(parseResponse(CHECK_REFUSAL), {
		version: 1,
		ok: false,
		code: "CONTRACT_UNREADABLE",
		message: "missing",
	});
	assert.deepEqual(
		parseResponse('{"version":1,"ok":true,"code":"OK","message":"","result":null}'),
		{ version: 1, ok: true, code: "OK", message: "", result: null },
	);
	for (const bad of [
		"bad",
		"null",
		"[]",
		"1",
		'"text"',
		"{}",
		'{"version":2,"ok":true,"code":"OK","message":""}',
		'{"version":1,"ok":"yes","code":"OK","message":""}',
		'{"version":1,"ok":true,"code":1,"message":""}',
		'{"version":1,"ok":true,"code":"OK","message":1}',
		'{"version":1,"ok":true,"code":"OTHER","message":""}',
		'{"version":1,"ok":false,"code":"OK","message":""}',
		'{"version":1,"ok":false,"code":"OTHER","message":""}',
	]) {
		assert.throws(() => parseResponse(bad), AdapterRefusal);
	}
});

test("delivery receipt parser closes shape and outcome", () => {
	assert.equal(parseDeliveryReceipt(DELIVERY_OK).candidate_commit, "candidate");
	assert.equal(parseDeliveryReceipt(DELIVERY_REFUSAL).code, "REPO_DIRTY");
	const valid = JSON.parse(DELIVERY_OK);
	const malformed = [
		"bad",
		"null",
		"[]",
		"1",
		'"text"',
		JSON.stringify({ ...valid, version: 2 }),
		JSON.stringify({ ...valid, outcome: "unknown" }),
		JSON.stringify({ ...valid, outcome: "discarded" }),
		JSON.stringify({ ...valid, code: 1 }),
		JSON.stringify({ ...valid, code: "UNKNOWN" }),
		JSON.stringify({ ...valid, message: 1 }),
		JSON.stringify({ ...valid, contract_id: 1 }),
		JSON.stringify({ ...valid, repository: null }),
		JSON.stringify({ ...valid, base_commit: 1 }),
		JSON.stringify({ ...valid, candidate_ref: 1 }),
		JSON.stringify({ ...valid, candidate_commit: 1 }),
		JSON.stringify({ ...valid, changed_paths: [1] }),
		JSON.stringify({ ...valid, command_exit: 1.5 }),
		JSON.stringify({ ...valid, worktree_path: 1 }),
		JSON.stringify({ ...valid, candidate_ref: null }),
		JSON.stringify({ ...valid, candidate_commit: null }),
		JSON.stringify({ ...valid, changed_paths: null }),
		JSON.stringify({ ...valid, changed_paths: {} }),
		JSON.stringify({ ...valid, command_exit: "0" }),
		JSON.stringify({ ...valid, outcome: "candidate-created", code: "FAILED" }),
	];
	for (const body of malformed) assert.throws(() => parseDeliveryReceipt(body), AdapterRefusal);
});

test("delivery invocation keeps inside contract relative and outside absolute", () => {
	const inside = buildDeliveryInvocation("/repo", "contracts/task.yaml", "-m", "/engine", 12);
	assert.equal(inside.command, "uv");
	assert.equal(inside.cwd, "/engine");
	assert.deepEqual(inside.args.slice(-3), ["--model=-m", "--", "contracts/task.yaml"]);
	assert.ok(inside.args.includes("12"));
	const outside = buildDeliveryInvocation("/repo", "/outside/task.yaml", "m", "/engine");
	assert.equal(outside.args.at(-1), "/outside/task.yaml");
	const root = buildDeliveryInvocation("/repo", "/repo", "m", "/engine");
	assert.equal(root.args.at(-1), "/repo");
	const relativeEngine = buildDeliveryInvocation("/repo", "-task.yaml", "m", "engine");
	assert.equal(relativeEngine.cwd, resolve("engine"));
	assert.deepEqual(relativeEngine.args.slice(-3), ["--model=m", "--", "-task.yaml"]);
});

test("one-shot exchange handles success and transport refusals", async () => {
	const settledChild = child({ stdout: CHECK_OK });
	assert.equal((await exchange(spawnerFor(settledChild), "{}", "/engine", 100)).code, "OK");
	const childWithoutStdinEvents = child({ stdout: CHECK_OK });
	delete childWithoutStdinEvents.stdin.on;
	assert.equal((await exchange(spawnerFor(childWithoutStdinEvents), "{}", "/engine", 100)).code, "OK");
	settledChild.emit("close", 0);
	settledChild.emit("error", new Error("late"));
	await refusal(exchange(spawnerFor(child({ stdout: "bad" })), "{}", "/engine", 100), "ENGINE_MALFORMED_RESPONSE");
	await refusal(exchange(spawnerFor(child({ exitCode: 1 })), "{}", "/engine", 100), "ENGINE_CRASHED");
	await refusal(exchange(() => { throw new Error("spawn"); }, "{}", "/engine", 100), "ENGINE_START_FAILED");
	await refusal(exchange(spawnerFor(child({ error: new Error("async") })), "{}", "/engine", 100), "ENGINE_START_FAILED");
	await refusal(exchange(spawnerFor(child({ writeError: new Error("write") })), "{}", "/engine", 100), "ENGINE_START_FAILED");
	const timedChild = child({ never: true });
	await refusal(exchange(spawnerFor(timedChild), "{}", "/engine", 1), "ENGINE_TIMEOUT");
	assert.equal(timedChild.killed, true);
	const asyncWriteChild = child({ never: true, asyncWriteError: new Error("pipe") });
	await refusal(exchange(spawnerFor(asyncWriteChild), "{}", "/engine", 100), "ENGINE_START_FAILED");
	const signalFailureChild = child({ never: true });
	signalFailureChild.kill = (signal) => {
		if (signal === "SIGKILL") queueMicrotask(() => signalFailureChild.emit("close", null));
		throw new Error(`cannot send ${signal}`);
	};
	await refusal(exchange(spawnerFor(signalFailureChild), "{}", "/engine", 1), "ENGINE_TIMEOUT");
	const synchronousCloseChild = child({ never: true });
	synchronousCloseChild.kill = () => synchronousCloseChild.emit("close", null);
	const synchronousClose = exchange(spawnerFor(synchronousCloseChild), "{}", "/engine", 100);
	synchronousCloseChild.emit("error", new Error("async"));
	await refusal(synchronousClose, "ENGINE_START_FAILED");
});

test("delivery drains diagnostics and accepts refusal receipts", async () => {
	const invocation = buildDeliveryInvocation("/repo", "task.yaml", "m", "/engine");
	const diagnostics = [];
	const settledChild = child({ stdout: DELIVERY_OK, stderr: "events" });
	assert.equal(
		(await runDelivery(spawnerFor(settledChild), invocation, 100, (chunk) => diagnostics.push(chunk))).code,
		"OK",
	);
	settledChild.emit("error", new Error("late"));
	settledChild.emit("close", 0);
	const childWithoutStdinEvents = child({ stdout: DELIVERY_OK });
	delete childWithoutStdinEvents.stdin.on;
	assert.equal((await runDelivery(spawnerFor(childWithoutStdinEvents), invocation, 100)).code, "OK");
	assert.deepEqual(diagnostics, ["events"]);
	assert.equal((await runDelivery(spawnerFor(child({ stdout: DELIVERY_REFUSAL, exitCode: 8 })), invocation, 100)).code, "REPO_DIRTY");
	const originalWrite = process.stderr.write;
	let defaultDiagnostic = "";
	process.stderr.write = (chunk) => { defaultDiagnostic += chunk; return true; };
	try {
		await runDelivery(spawnerFor(child({ stdout: DELIVERY_OK, stderr: "default" })), invocation, 100);
	} finally {
		process.stderr.write = originalWrite;
	}
	assert.equal(defaultDiagnostic, "default");
});

test("delivery converts every transport failure", async () => {
	const invocation = buildDeliveryInvocation("/repo", "task.yaml", "m", "/engine");
	await refusal(runDelivery(() => { throw new Error("spawn"); }, invocation, 100), "ENGINE_START_FAILED");
	await refusal(runDelivery(spawnerFor(child({ error: new Error("async") })), invocation, 100), "ENGINE_START_FAILED");
	await refusal(runDelivery(spawnerFor(child({ never: true, asyncWriteError: new Error("pipe") })), invocation, 100), "ENGINE_START_FAILED");
	await refusal(runDelivery(spawnerFor(child({ endError: new Error("end") })), invocation, 100), "ENGINE_START_FAILED");
	await refusal(runDelivery(spawnerFor(child({ stdout: "bad" })), invocation, 100), "ENGINE_MALFORMED_RESPONSE");
	await refusal(runDelivery(spawnerFor(child({ stdout: "bad", exitCode: 1 })), invocation, 100), "ENGINE_CRASHED");
	await refusal(runDelivery(spawnerFor(child({ stdout: "x".repeat(MAX_RECEIPT_BYTES + 1) })), invocation, 100), "ENGINE_MALFORMED_RESPONSE");
	const timedChild = child({ never: true, ignoreTerm: true });
	await refusal(runDelivery(spawnerFor(timedChild), invocation, 1, () => {}, 5), "ENGINE_TIMEOUT");
	assert.deepEqual(timedChild.killSignals, ["SIGTERM", "SIGKILL"]);
	const signalFailureChild = child({ never: true });
	signalFailureChild.kill = (signal) => {
		if (signal === "SIGKILL") queueMicrotask(() => signalFailureChild.emit("close", null));
		throw new Error(`cannot send ${signal}`);
	};
	await refusal(
		runDelivery(spawnerFor(signalFailureChild), invocation, 1, () => {}, 5),
		"ENGINE_TIMEOUT",
	);
	const synchronousCloseChild = child({ never: true });
	synchronousCloseChild.kill = () => synchronousCloseChild.emit("close", null);
	const synchronousClose = runDelivery(spawnerFor(synchronousCloseChild), invocation, 100);
	synchronousCloseChild.emit("error", new Error("async"));
	await refusal(synchronousClose, "ENGINE_START_FAILED");
});

test("implement handler reports configuration, success, refusal, and crash", async () => {
	const savedRepo = process.env.SATYRN_ENGINE_REPO;
	const savedModel = process.env.SATYRN_MODEL;
	const notifications = [];
	const ctx = { cwd: "/repo", ui: { notify(message, level) { notifications.push({ message, level }); } } };
	try {
		delete process.env.SATYRN_ENGINE_REPO;
		delete process.env.SATYRN_MODEL;
		await createAdapter(spawnerFor(child())).implement("task.yaml", ctx);
		process.env.SATYRN_ENGINE_REPO = "/engine";
		await createAdapter(spawnerFor(child())).implement("task.yaml", ctx);
		process.env.SATYRN_MODEL = "m";
		await createAdapter(spawnerFor(child())).implement("  ", ctx);
		await createAdapter(spawnerFor(child({ stdout: DELIVERY_OK })), 100).implement("task.yaml", ctx);
		await createAdapter(spawnerFor(child({ stdout: DELIVERY_REFUSAL, exitCode: 8 })), 100).implement("task.yaml", ctx);
		await createAdapter(spawnerFor(child({ stdout: "bad" })), 100).implement("task.yaml", ctx);
		const unexpectedChild = child();
		Object.defineProperty(unexpectedChild, "stdout", {
			get() {
				throw new Error("unexpected transport error");
			},
		});
		await createAdapter(spawnerFor(unexpectedChild), 1).implement("task.yaml", ctx);
		const nonErrorChild = child();
		Object.defineProperty(nonErrorChild, "stdout", {
			get() {
				throw "non-error transport failure";
			},
		});
		await createAdapter(spawnerFor(nonErrorChild), 1).implement("task.yaml", ctx);
	} finally {
		if (savedRepo === undefined) delete process.env.SATYRN_ENGINE_REPO;
		else process.env.SATYRN_ENGINE_REPO = savedRepo;
		if (savedModel === undefined) delete process.env.SATYRN_MODEL;
		else process.env.SATYRN_MODEL = savedModel;
	}
	assert.match(notifications[0].message, /SATYRN_ENGINE_REPO/);
	assert.match(notifications[1].message, /SATYRN_MODEL/);
	assert.match(notifications[2].message, /USAGE/);
	assert.match(notifications[3].message, /refs\/satyrn\/candidates/);
	assert.match(notifications[4].message, /REPO_DIRTY/);
	assert.match(notifications[5].message, /ENGINE_MALFORMED_RESPONSE/);
	assert.match(notifications[6].message, /ENGINE_START_FAILED.*unexpected transport error/);
	assert.match(notifications[7].message, /ENGINE_START_FAILED.*non-error transport failure/);
});

test("implement contains unexpected notification failures", async () => {
	const savedRepo = process.env.SATYRN_ENGINE_REPO;
	const savedModel = process.env.SATYRN_MODEL;
	process.env.SATYRN_ENGINE_REPO = "/engine";
	process.env.SATYRN_MODEL = "m";
	try {
		for (const thrown of [new Error("ui failed"), "ui failed without Error"]) {
			const notifications = [];
			let first = true;
			const ctx = {
				cwd: "/repo",
				ui: {
					notify(message, level) {
						if (first) {
							first = false;
							throw thrown;
						}
						notifications.push({ message, level });
					},
				},
			};
			await createAdapter(spawnerFor(child({ stdout: DELIVERY_OK })), 100).implement("task.yaml", ctx);
			assert.match(notifications[0].message, /ADAPTER_ERROR.*ui failed/);
		}
	} finally {
		if (savedRepo === undefined) delete process.env.SATYRN_ENGINE_REPO;
		else process.env.SATYRN_ENGINE_REPO = savedRepo;
		if (savedModel === undefined) delete process.env.SATYRN_MODEL;
		else process.env.SATYRN_MODEL = savedModel;
	}
});

test("default extension registers the E5 command", () => {
	let registration;
	registerExtension({ registerCommand(name, value) { registration = { name, value }; } });
	assert.equal(registration.name, "implement");
	assert.match(registration.value.description, /isolated model attempt/);
	assert.equal(typeof registration.value.handler, "function");
});
