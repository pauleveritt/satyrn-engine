/** Node tests for the E2 transport and E5 delivery adapter. */

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { test } from "node:test";

import registerExtension, {
	AdapterRefusal,
	MAX_RECEIPT_BYTES,
	buildDeliveryInvocation,
	buildDeriveInvocation,
	buildRequest,
	createAdapter,
	exchange,
	parseDeliveryReceipt,
	parseResponse,
	processControlForPlatform,
	receiptSummary,
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
	validation: "passed",
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
		stdoutError = null,
		stderrError = null,
		ignoreTerm = false,
		pid = undefined,
		autoSpawn = true,
	} = options;
	const listeners = { spawn: [], close: [], error: [] };
	const killSignals = [];
	const stdinListeners = { error: [] };
	let closed = false;
	const emitClose = () => {
		if (closed) return;
		closed = true;
		for (const callback of listeners.close) callback(exitCode);
	};
	const result = {
		pid,
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
				if (event === "data" && stdout && !stdoutError) queueMicrotask(() => callback(stdout));
				if (event === "error" && stdoutError) queueMicrotask(() => callback(stdoutError));
			},
		},
		stderr: {
			on(event, callback) {
				if (event === "data" && stderr && !stderrError) queueMicrotask(() => callback(stderr));
				if (event === "error" && stderrError) queueMicrotask(() => callback(stderrError));
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
	if (autoSpawn && !error) queueMicrotask(() => { for (const callback of listeners.spawn) callback(); });
	if (error) queueMicrotask(() => { for (const callback of listeners.error) callback(error); });
	return result;
}

function spawnerFor(value) {
	return () => value;
}

const DIRECT_CONTROL = { kind: "direct-child" };

function runDirectDelivery(spawner, invocation, deadlineMs, diagnostic, terminationGraceMs) {
	return runDelivery(spawner, invocation, deadlineMs, diagnostic, terminationGraceMs, DIRECT_CONTROL);
}

function createDirectAdapter(spawner, deadlineMs, writeReceipt) {
	return createAdapter(spawner, deadlineMs, DIRECT_CONTROL, writeReceipt);
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
	assert.equal(parseDeliveryReceipt(DELIVERY_OK).tampered, undefined);
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

test("a contract under the git dir reaches attempt as an absolute path", () => {
	const invocation = buildDeliveryInvocation("/repo", "/repo/.git/satyrn/contracts/implement-abc.yaml", "m", "/engine");
	assert.equal(invocation.args.at(-1), "/repo/.git/satyrn/contracts/implement-abc.yaml");
	assert.equal(buildDeliveryInvocation("/repo", "/repo/contract.yaml", "m", "/engine").args.at(-1), "contract.yaml");
});

test("derive invocation runs the engine's derive subcommand with -- before the request", () => {
	assert.deepEqual(buildDeriveInvocation("/repo", "-add a flag to src/cli.py", "/engine"), {
		command: "uv", cwd: "/engine",
		args: ["run", "--project", "/engine", "satyrn-engine", "derive", "--repo", "/repo", "--", "-add a flag to src/cli.py"],
	});
});

test("delivery invocation keeps inside contract relative and outside absolute", () => {
	const inside = buildDeliveryInvocation("/repo", "contracts/task.yaml", "-m", "/engine", 12);
	assert.equal(inside.command, "uv");
	assert.equal(inside.cwd, "/engine");
	assert.deepEqual(inside.args.slice(-3), ["--model=-m", "--", "contracts/task.yaml"]);
	assert.ok(inside.args.includes("12"));
	const outside = buildDeliveryInvocation("/repo", "/outside/task.yaml", "m", "/engine");
	assert.equal(outside.args.at(-1), "/outside/task.yaml");
	const hidden = buildDeliveryInvocation("/repo", "..hidden.yaml", "m", "/engine");
	assert.equal(hidden.args.at(-1), "..hidden.yaml");
	const parent = buildDeliveryInvocation("/repo", "../task.yaml", "m", "/engine");
	assert.equal(parent.args.at(-1), "/task.yaml");
	const root = buildDeliveryInvocation("/repo", "/repo", "m", "/engine");
	assert.equal(root.args.at(-1), "/repo");
	const relativeEngine = buildDeliveryInvocation("/repo", "-task.yaml", "m", "engine");
	assert.equal(relativeEngine.cwd, resolve("engine"));
	assert.deepEqual(relativeEngine.args.slice(-3), ["--model=m", "--", "-task.yaml"]);
});

test("delivery invocation classifies existing path aliases by filesystem identity", () => {
	const parent = mkdtempSync(join(tmpdir(), "satyrn-engine-alias-"));
	try {
		const repo = join(parent, "repo");
		const repoAlias = join(parent, "repo-link");
		const contract = join(repo, "task.yaml");
		const contractAlias = join(parent, "task-link.yaml");
		mkdirSync(repo);
		writeFileSync(contract, "id: alias\n");
		symlinkSync(repo, repoAlias, "dir");
		symlinkSync(contract, contractAlias, "file");

		assert.equal(buildDeliveryInvocation(repoAlias, contract, "m", "/engine").args.at(-1), "task.yaml");
		assert.equal(buildDeliveryInvocation(repo, contractAlias, "m", "/engine").args.at(-1), "task.yaml");
	} finally {
		rmSync(parent, { recursive: true, force: true });
	}
});

test("one-shot exchange runs the protocol subcommand with --no-sync (a read-only export cannot be re-synced)", async () => {
	let seenArgs = null;
	const recordingSpawner = (command, args, options) => {
		seenArgs = args;
		return child({ stdout: CHECK_OK });
	};
	assert.equal((await exchange(recordingSpawner, "{}", "/engine", 100)).code, "OK");
	assert.deepEqual(seenArgs, ["run", "--no-sync", "--project", "/engine", "satyrn-engine", "protocol"]);
});

test("derive and delivery invocations still let uv sync (a developer's first /implement needs it)", () => {
	assert.ok(!buildDeriveInvocation("/repo", "request", "/engine").args.includes("--no-sync"));
	assert.ok(!buildDeliveryInvocation("/repo", "task.yaml", "m", "/engine").args.includes("--no-sync"));
});

test("one-shot exchange handles success and transport refusals", async () => {
	const settledChild = child({ stdout: CHECK_OK, stderr: "ignored diagnostic" });
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
	for (const streamFailure of [
		child({ never: true, stdoutError: new Error("stdout") }),
		child({ never: true, stderrError: new Error("stderr") }),
	]) {
		await refusal(exchange(spawnerFor(streamFailure), "{}", "/engine", 100), "ENGINE_START_FAILED");
		assert.deepEqual(streamFailure.killSignals, ["SIGTERM"]);
	}
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
		(await runDirectDelivery(spawnerFor(settledChild), invocation, 100, (chunk) => diagnostics.push(chunk))).code,
		"OK",
	);
	settledChild.emit("error", new Error("late"));
	settledChild.emit("close", 0);
	const childWithoutStdinEvents = child({ stdout: DELIVERY_OK });
	delete childWithoutStdinEvents.stdin.on;
	assert.equal((await runDirectDelivery(spawnerFor(childWithoutStdinEvents), invocation, 100)).code, "OK");
	assert.deepEqual(diagnostics, ["events"]);
	assert.equal((await runDirectDelivery(spawnerFor(child({ stdout: DELIVERY_REFUSAL, exitCode: 8 })), invocation, 100)).code, "REPO_DIRTY");
	const originalWrite = process.stderr.write;
	let defaultDiagnostic = "";
	process.stderr.write = (chunk) => { defaultDiagnostic += chunk; return true; };
	try {
		await runDirectDelivery(spawnerFor(child({ stdout: DELIVERY_OK, stderr: "default" })), invocation, 100);
	} finally {
		process.stderr.write = originalWrite;
	}
	assert.equal(defaultDiagnostic, "default");
});

test("delivery converts every transport failure", async () => {
	const invocation = buildDeliveryInvocation("/repo", "task.yaml", "m", "/engine");
	await refusal(runDirectDelivery(() => { throw new Error("spawn"); }, invocation, 100), "ENGINE_START_FAILED");
	await refusal(runDirectDelivery(spawnerFor(child({ error: new Error("async") })), invocation, 100), "ENGINE_START_FAILED");
	await refusal(runDirectDelivery(spawnerFor(child({ never: true, asyncWriteError: new Error("pipe") })), invocation, 100), "ENGINE_START_FAILED");
	await refusal(runDirectDelivery(spawnerFor(child({ endError: new Error("end") })), invocation, 100), "ENGINE_START_FAILED");
	await refusal(
		runDirectDelivery(spawnerFor(child({ never: true, stdoutError: new Error("stdout") })), invocation, 100),
		"ENGINE_START_FAILED",
	);
	await refusal(
		runDirectDelivery(spawnerFor(child({ never: true, stderrError: new Error("stderr") })), invocation, 100),
		"ENGINE_START_FAILED",
	);
	const diagnosticFailure = child({ never: true, stderr: "diagnostic" });
	await refusal(
		runDirectDelivery(spawnerFor(diagnosticFailure), invocation, 100, () => { throw new Error("sink"); }),
		"ADAPTER_ERROR",
	);
	assert.deepEqual(diagnosticFailure.killSignals, ["SIGTERM"]);
	await refusal(runDirectDelivery(spawnerFor(child({ stdout: "bad" })), invocation, 100), "ENGINE_MALFORMED_RESPONSE");
	await refusal(runDirectDelivery(spawnerFor(child({ stdout: "bad", exitCode: 1 })), invocation, 100), "ENGINE_CRASHED");
	await refusal(runDirectDelivery(spawnerFor(child({ stdout: "x".repeat(MAX_RECEIPT_BYTES + 1) })), invocation, 100), "ENGINE_MALFORMED_RESPONSE");
	const timedChild = child({ never: true, ignoreTerm: true });
	await refusal(runDirectDelivery(spawnerFor(timedChild), invocation, 1, () => {}, 5), "ENGINE_TIMEOUT");
	assert.deepEqual(timedChild.killSignals, ["SIGTERM", "SIGKILL"]);
	const ignoredStreamFailure = child({ never: true, ignoreTerm: true, stdoutError: new Error("stream") });
	await refusal(
		runDirectDelivery(spawnerFor(ignoredStreamFailure), invocation, 100, () => {}, 5),
		"ENGINE_START_FAILED",
	);
	assert.deepEqual(ignoredStreamFailure.killSignals, ["SIGTERM", "SIGKILL"]);
	const signalFailureChild = child({ never: true });
	signalFailureChild.kill = (signal) => {
		if (signal === "SIGKILL") queueMicrotask(() => signalFailureChild.emit("close", null));
		throw new Error(`cannot send ${signal}`);
	};
	await refusal(
		runDirectDelivery(spawnerFor(signalFailureChild), invocation, 1, () => {}, 5),
		"ENGINE_TIMEOUT",
	);
	const synchronousCloseChild = child({ never: true });
	synchronousCloseChild.kill = () => synchronousCloseChild.emit("close", null);
	const synchronousClose = runDirectDelivery(spawnerFor(synchronousCloseChild), invocation, 100);
	synchronousCloseChild.emit("error", new Error("async"));
	await refusal(synchronousClose, "ENGINE_START_FAILED");
});

test("delivery preserves close-time refusal codes until its POSIX group is gone", async () => {
	const invocation = buildDeliveryInvocation("/repo", "task.yaml", "m", "/engine");
	for (const [options, expected] of [
		[{ stdout: "bad", pid: 4101 }, "ENGINE_MALFORMED_RESPONSE"],
		[{ stdout: "bad", exitCode: 1, pid: 4102 }, "ENGINE_CRASHED"],
		[{ stdout: "x".repeat(MAX_RECEIPT_BYTES + 1), pid: 4103 }, "ENGINE_MALFORMED_RESPONSE"],
	]) {
		const signals = [];
		const control = { kind: "posix-group", signal(_pgid, signal) {
			signals.push(signal);
			return signal === 0 ? { kind: "gone" } : { kind: "present" };
		} };
		await refusal(runDelivery(spawnerFor(child(options)), invocation, 100, () => {}, 5, control), expected);
		assert.deepEqual(signals, ["SIGTERM", 0]);
	}
});

test("delivery waits for spawn, child close, and process-group disappearance", async () => {
	const invocation = buildDeliveryInvocation("/repo", "task.yaml", "m", "/engine");
	const managed = child({ never: true, pid: 4242, autoSpawn: false });
	const groupSignals = [];
	let probes = 0;
	let spawnOptions;
	const control = { kind: "posix-group", signal(pgid, signal) {
		assert.equal(pgid, 4242);
		if (signal === 0) {
			probes += 1;
			if (probes === 1) return { kind: "unknown", detail: "probe denied" };
			return probes === 2 ? { kind: "present" } : { kind: "gone" };
		}
		groupSignals.push(signal);
		if (signal === "SIGTERM") managed.emit("close", null);
		return { kind: "present" };
	} };
	let completed = false;
	const pending = runDelivery(
		(_command, _args, options) => {
			spawnOptions = options;
			return managed;
		},
		invocation,
		1,
		() => {},
		20,
		control,
	).finally(() => { completed = true; });
	await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
	assert.deepEqual(groupSignals, []);
	assert.equal(completed, false);
	managed.emit("spawn");
	await refusal(pending, "ENGINE_TIMEOUT");
	assert.deepEqual(spawnOptions, { cwd: "/engine", detached: true, windowsHide: true });
	assert.deepEqual(groupSignals, ["SIGTERM"]);
	assert.deepEqual(managed.killSignals, []);

	const closesLast = child({ never: true, pid: 4243, autoSpawn: false });
	let closesLastCompleted = false;
	const closesLastPending = runDelivery(
		spawnerFor(closesLast),
		invocation,
		1,
		() => {},
		20,
		{ kind: "posix-group", signal: () => ({ kind: "gone" }) },
	).finally(() => { closesLastCompleted = true; });
	await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
	closesLast.emit("spawn");
	await new Promise((resolvePromise) => setTimeout(resolvePromise, 1));
	assert.equal(closesLastCompleted, false);
	closesLast.emit("close", null);
	await refusal(closesLastPending, "ENGINE_TIMEOUT");

	const spawnFailure = child({ never: true, autoSpawn: false });
	let spawnFailureCompleted = false;
	const spawnFailurePending = runDelivery(
		spawnerFor(spawnFailure), invocation, 1, () => {}, 20,
		{ kind: "posix-group", signal: () => ({ kind: "gone" }) },
	).finally(() => { spawnFailureCompleted = true; });
	await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
	spawnFailure.emit("close", null);
	await new Promise((resolvePromise) => setTimeout(resolvePromise, 1));
	assert.equal(spawnFailureCompleted, false);
	spawnFailure.emit("error", new Error("spawn failed"));
	await refusal(spawnFailurePending, "ENGINE_TIMEOUT");

	const fallback = child({ never: true, pid: 4343, autoSpawn: false });
	const fallbackSignals = [];
	fallback.kill = (signal) => {
		fallbackSignals.push(signal);
		fallback.emit("close", null);
	};
	const fallbackPending = runDelivery(
		spawnerFor(fallback), invocation, 1, () => {}, 5, { kind: "direct-child" },
	);
	await new Promise((resolvePromise) => setTimeout(resolvePromise, 5));
	fallback.emit("spawn");
	await refusal(fallbackPending, "ENGINE_TIMEOUT");
	await new Promise((resolvePromise) => setTimeout(resolvePromise, 10));
	assert.deepEqual(fallbackSignals, ["SIGTERM"]);
});

test("default POSIX process control distinguishes live, gone, and unknown groups", () => {
	assert.deepEqual(processControlForPlatform("win32"), { kind: "direct-child" });
	const control = processControlForPlatform("darwin");
	assert.equal(control.kind, "posix-group");
	const originalKill = process.kill;
	let probes = 0;
	try {
		process.kill = (pid, signal) => {
			assert.equal(pid, -1234);
			if (signal === "SIGTERM") return true;
			probes += 1;
			if (probes === 1) throw Object.assign(new Error("gone"), { code: "ESRCH" });
			throw Object.assign(new Error("denied"), { code: "EPERM" });
		};
		assert.deepEqual(control.signal(1234, "SIGTERM"), { kind: "present" });
		assert.deepEqual(control.signal(1234, 0), { kind: "gone" });
		assert.deepEqual(control.signal(1234, 0), { kind: "unknown", detail: "Error: denied" });
	} finally {
		process.kill = originalKill;
	}
});

const okReceipt = (extra = {}) => JSON.stringify({ ...JSON.parse(DELIVERY_OK), ...extra });
const ui = (notes, confirmAnswer) => ({ notify: (m, l) => notes.push([l, m]), confirm: async () => confirmAnswer });

test("/implement with a request derives and shows the contract; without a UI it waits for --go", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const spawned = [];
	const spawner = (command, args) => { spawned.push([command, ...args]); return child({ stdout: "id: implement-0123456789ab\ntask: add a flag\n", stderr: "satyrn-engine: contract /repo/.git/satyrn/contracts/implement-0123456789ab.yaml\n" }); };
	const notes = [];
	await createAdapter(spawner).implement("add a flag to src/cli.py", { cwd: "/repo", hasUI: false, ui: ui(notes, false) });
	assert.equal(spawned.length, 1);
	assert.deepEqual(spawned[0].slice(0, 6), ["uv", "run", "--project", "/engine", "satyrn-engine", "derive"]);
	assert.deepEqual(notes[0], ["info", "id: implement-0123456789ab\ntask: add a flag\n"]);
	assert.match(notes[1][1], /run \/implement --go implement-0123456789ab to dispatch/);
});

test("--gopher is not mistaken for --go and is derived as a request", () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const spawned = [];
	const spawner = (command, args) => { spawned.push([command, ...args]); return child({ stdout: "id: implement-0123456789ab\ntask: t\n", stderr: "satyrn-engine: contract /repo/.git/satyrn/contracts/implement-0123456789ab.yaml\n" }); };
	const notes = [];
	return createAdapter(spawner).implement("--gopher src/a.py", { cwd: "/repo", hasUI: false, ui: ui(notes, false) }).then(() => {
		assert.deepEqual(spawned[0].slice(0, 6), ["uv", "run", "--project", "/engine", "satyrn-engine", "derive"]);
		assert.ok(spawned[0].includes("--gopher src/a.py"));
	});
});

test("the no-UI dispatch hint keeps only the contract line, dropping any warning before it", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const spawner = () => child({
		stdout: "id: implement-0123456789ab\ntask: t\n",
		stderr: "uv warning: something noisy\nsatyrn-engine: contract /repo/.git/satyrn/contracts/implement-0123456789ab.yaml\n",
	});
	const notes = [];
	await createAdapter(spawner).implement("t src/a.py", { cwd: "/repo", hasUI: false, ui: ui(notes, false) });
	assert.doesNotMatch(notes.at(-1)[1], /uv warning/);
	assert.match(notes.at(-1)[1], /^satyrn-engine: contract \/repo\/\.git\/satyrn\/contracts\/implement-0123456789ab\.yaml; run \/implement --go implement-0123456789ab to dispatch$/);
});

test("/implement with a UI confirms in place and dispatches on yes, waits on no", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	for (const answer of [true, false]) {
		const spawned = [];
		const spawner = (command, args) => {
			spawned.push([command, ...args]);
			if (command === "git") return child({ stdout: "/repo/.git\n" });
			if (args.includes("derive")) return child({ stdout: "id: implement-0123456789ab\ntask: t\n", stderr: "satyrn-engine: contract /repo/.git/satyrn/contracts/implement-0123456789ab.yaml\n" });
			return child({ stdout: okReceipt() });
		};
		const notes = [];
		await createAdapter(spawner, undefined, DIRECT_CONTROL, () => {}).implement("t src/a.py", { cwd: "/repo", hasUI: true, ui: ui(notes, answer) });   // fake receipt writer: never touch /repo (m8)
		assert.equal(spawned.some((argv) => argv.includes("deliver")), answer);
		if (!answer) assert.match(notes.at(-1)[1], /not dispatched; run \/implement --go implement-0123456789ab/);
	}
});

test("/implement --go resolves the contract under the git dir, dispatches, writes the receipt and summarizes it", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const spawned = []; const written = [];
	const spawner = (command, args) => {
		spawned.push([command, ...args]);
		if (command === "git") return child({ stdout: "/repo/.git\n" });
		return child({ stdout: okReceipt({ code: "TESTS_FAILED", validation: "failed", tokens_out: 1200, turns: 7,
			guard_firings: { loop_broken: 0, scope_refused: 1, symbol_preserved: 0, command_bounded: 2, command_timed_out: 0 } }) });
	};
	const notes = [];
	await createAdapter(spawner, undefined, DIRECT_CONTROL, (path, text) => written.push([path, text])).implement("--go implement-0123456789ab", { cwd: "/repo", hasUI: false, ui: ui(notes, false) });
	assert.deepEqual(spawned[0], ["git", "-C", "/repo", "rev-parse", "--path-format=absolute", "--git-dir"]);
	assert.equal(spawned[1].at(-1), "/repo/.git/satyrn/contracts/implement-0123456789ab.yaml");
	assert.equal(written[0][0], "/repo/.git/satyrn/receipts/implement-0123456789ab.json");
	assert.deepEqual(notes.at(-1), ["error", "satyrn-engine: TESTS_FAILED: refs/satyrn/candidates/task/head candidate validation=failed tokens_out=1200 turns=7 guards: scope_refused=1 command_bounded=2"]);
});

test("--go writes the engine's raw receipt verbatim, keeping fields the parser does not model", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const raw = okReceipt({
		budget: { state: "token_exhausted" },
		carried: { tampered: ["conftest.py"], absent: [] },
		validation_exit: 1,
		validation_output: "FAILED tests/test_app.py",
		tool_calls: 5,
		tokens_in: 300,
	});
	const spawner = (command) => (command === "git" ? child({ stdout: "/repo/.git\n" }) : child({ stdout: raw }));
	const written = [];
	const notes = [];
	await createAdapter(spawner, undefined, DIRECT_CONTROL, (path, text) => written.push([path, text])).implement("--go implement-0123456789ab", { cwd: "/repo", hasUI: false, ui: ui(notes, false) });
	const [, text] = written[0];
	assert.equal(text, `${raw}\n`);
	const parsed = JSON.parse(text);
	assert.deepEqual(parsed.budget, { state: "token_exhausted" });
	assert.deepEqual(parsed.carried, { tampered: ["conftest.py"], absent: [] });
	assert.equal(parsed.validation_exit, 1);
	assert.equal(parsed.validation_output, "FAILED tests/test_app.py");
	assert.equal(parsed.tool_calls, 5);
	assert.equal(parsed.tokens_in, 300);
});

test("/implement --go with a bad id, and a failed derive, are named refusals", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const notes = [];
	const ctx = { cwd: "/repo", hasUI: false, ui: ui(notes, false) };
	await createAdapter(() => child({ stdout: "", stderr: "satyrn-engine: DERIVE: name at least one tracked file\n", exitCode: 5 })).implement("make it faster", ctx);
	assert.deepEqual(notes.at(-1), ["error", "satyrn-engine: DERIVE: name at least one tracked file"]);
	await createAdapter(() => child({})).implement("--go ../etc", ctx);
	assert.deepEqual(notes.at(-1), ["error", "satyrn-engine: USAGE: --go takes a contract id like implement-0123456789ab"]);
});

test("R13: a clean pass is info, every other validation outcome for code OK is error", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	for (const [validation, expectedLevel] of [["passed", "info"], ["unavailable", "error"]]) {
		const spawner = (command) => {
			if (command === "git") return child({ stdout: "/repo/.git\n" });
			return child({ stdout: okReceipt({ validation }) });
		};
		const notes = [];
		await createAdapter(spawner, undefined, DIRECT_CONTROL, () => {}).implement("--go implement-0123456789ab", { cwd: "/repo", hasUI: false, ui: ui(notes, false) });
		assert.equal(notes.at(-1)[0], expectedLevel, `validation=${validation} should notify at ${expectedLevel}`);
		assert.match(notes.at(-1)[1], /satyrn-engine: OK: /);
	}
});

test("parseDeliveryReceipt copies carried.tampered when present", () => {
	const withTampered = JSON.parse(DELIVERY_OK);
	withTampered.carried = { tampered: ["a.py", "b.py"], absent: [] };
	assert.deepEqual(parseDeliveryReceipt(JSON.stringify(withTampered)).tampered, ["a.py", "b.py"]);
	const emptyTampered = JSON.parse(DELIVERY_OK);
	emptyTampered.carried = { tampered: [], absent: [] };
	assert.deepEqual(parseDeliveryReceipt(JSON.stringify(emptyTampered)).tampered, []);
});

test("final review fix (extends R13): a tampered carried set is error even with code OK and validation passed", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const spawner = (command) => {
		if (command === "git") return child({ stdout: "/repo/.git\n" });
		return child({ stdout: okReceipt({ validation: "passed", carried: { tampered: ["tests/conftest.py"], absent: [] } }) });
	};
	const notes = [];
	await createAdapter(spawner, undefined, DIRECT_CONTROL, () => {}).implement("--go implement-0123456789ab", { cwd: "/repo", hasUI: false, ui: ui(notes, false) });
	assert.equal(notes.at(-1)[0], "error");
	assert.match(notes.at(-1)[1], /tampered=tests\/conftest\.py/);
});

test("OK + passed + no tampered still notifies info (the existing R13 case is unchanged)", async () => {
	process.env.SATYRN_ENGINE_REPO = "/engine"; process.env.SATYRN_MODEL = "m";
	const spawner = (command) => {
		if (command === "git") return child({ stdout: "/repo/.git\n" });
		return child({ stdout: okReceipt({ validation: "passed", carried: { tampered: [], absent: [] } }) });
	};
	const notes = [];
	await createAdapter(spawner, undefined, DIRECT_CONTROL, () => {}).implement("--go implement-0123456789ab", { cwd: "/repo", hasUI: false, ui: ui(notes, false) });
	assert.equal(notes.at(-1)[0], "info");
	assert.doesNotMatch(notes.at(-1)[1], /tampered=/);
});

test("receiptSummary formats validation, tokens_out, turns, nonzero guard firings and a tampered list", () => {
	assert.equal(receiptSummary({ validation: "passed", tokens_out: 10, turns: 2, guard_firings: {} }),
		"validation=passed tokens_out=10 turns=2 guards: none");
	assert.equal(receiptSummary({ guard_firings: {}, tampered: ["a.py", "b.py"] }),
		"validation=unknown tokens_out=0 turns=0 guards: none tampered=a.py,b.py");
	assert.equal(receiptSummary({ guard_firings: { scope_refused: 1, command_bounded: 0 } }),
		"validation=unknown tokens_out=0 turns=0 guards: scope_refused=1");
});

// gitThenSpawner: the first spawn (the `--go` git rev-parse lookup) always
// resolves to a fake git dir under /repo; the next spawn (the delivery
// itself) is whatever the scenario needs. Never touches the real /repo.
function gitThenSpawner(next) {
	return (command) => (command === "git" ? child({ stdout: "/repo/.git\n" }) : next());
}

test("implement handler reports configuration, --go success, refusal, and crash", async () => {
	const savedRepo = process.env.SATYRN_ENGINE_REPO;
	const savedModel = process.env.SATYRN_MODEL;
	const notifications = [];
	const noopWriteReceipt = () => {}; // fake receipt writer: never touch /repo (m8)
	const ctx = { cwd: "/repo", hasUI: false, ui: { notify(message, level) { notifications.push({ message, level }); }, confirm: async () => false } };
	try {
		delete process.env.SATYRN_ENGINE_REPO;
		delete process.env.SATYRN_MODEL;
		await createDirectAdapter(spawnerFor(child())).implement("--go implement-0123456789ab", ctx);
		process.env.SATYRN_ENGINE_REPO = "/engine";
		await createDirectAdapter(spawnerFor(child())).implement("--go implement-0123456789ab", ctx);
		process.env.SATYRN_MODEL = "m";
		await createDirectAdapter(spawnerFor(child())).implement("  ", ctx);
		await createDirectAdapter(gitThenSpawner(() => child({ stdout: DELIVERY_OK })), 100, noopWriteReceipt).implement("--go implement-0123456789ab", ctx);
		await createDirectAdapter(gitThenSpawner(() => child({ stdout: DELIVERY_REFUSAL, exitCode: 8 })), 100, noopWriteReceipt).implement("--go implement-0123456789ab", ctx);
		await createDirectAdapter(gitThenSpawner(() => child({ stdout: "bad" })), 100, noopWriteReceipt).implement("--go implement-0123456789ab", ctx);
		// Built lazily, inside the spawner: child()'s one-shot "spawn"
		// microtask must fire after runDelivery attaches its listener, not
		// before (the git collect step already consumed a few microtask
		// turns by the time runDelivery's spawner call reaches here).
		await createDirectAdapter(gitThenSpawner(() => {
			const unexpectedChild = child();
			Object.defineProperty(unexpectedChild, "stdout", {
				get() {
					throw new Error("unexpected transport error");
				},
			});
			return unexpectedChild;
		}), 1, noopWriteReceipt).implement("--go implement-0123456789ab", ctx);
		await createDirectAdapter(gitThenSpawner(() => {
			const nonErrorChild = child();
			Object.defineProperty(nonErrorChild, "stdout", {
				get() {
					throw "non-error transport failure";
				},
			});
			return nonErrorChild;
		}), 1, noopWriteReceipt).implement("--go implement-0123456789ab", ctx);
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
				hasUI: false,
				ui: {
					notify(message, level) {
						if (first) {
							first = false;
							throw thrown;
						}
						notifications.push({ message, level });
					},
					confirm: async () => false,
				},
			};
			await createDirectAdapter(gitThenSpawner(() => child({ stdout: DELIVERY_OK })), 100, () => {}).implement("--go implement-0123456789ab", ctx);
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
	assert.match(registration.value.description, /Derive a contract from a request/);
	assert.equal(typeof registration.value.handler, "function");
});
