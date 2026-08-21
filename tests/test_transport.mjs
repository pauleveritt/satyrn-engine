import assert from "node:assert/strict";
import { test } from "node:test";

import {
	AdapterRefusal,
	TERMINATION_GRACE_MS,
	exchange,
} from "../packages/engine/orchestrator.ts";

const RESPONSE = JSON.stringify({ version: 1, ok: true, code: "OK", message: "" });

function child({ stdout = RESPONSE, stdinFailure, ignoreTerm = false, never = false } = {}) {
	const listeners = { close: [], error: [], stdinError: [] };
	const signals = [];
	let closed = false;
	const close = (code) => {
		if (closed) return;
		closed = true;
		for (const callback of listeners.close) callback(code);
	};
	return {
		stdin: {
			write() {
				if (stdinFailure !== undefined) {
					queueMicrotask(() => {
						for (const callback of listeners.stdinError) callback(stdinFailure);
					});
				}
			},
			end() {
				if (stdinFailure === undefined && !never) queueMicrotask(() => close(0));
			},
			on(event, callback) {
				assert.equal(event, "error");
				listeners.stdinError.push(callback);
			},
		},
		stdout: {
			on(event, callback) {
				assert.equal(event, "data");
				if (stdinFailure === undefined && !never) queueMicrotask(() => callback(stdout));
			},
		},
		on(event, callback) {
			listeners[event].push(callback);
		},
		kill(signal = "SIGTERM") {
			signals.push(signal);
			if (!ignoreTerm || signal === "SIGKILL") queueMicrotask(() => close(null));
			return true;
		},
		get signals() {
			return signals;
		},
	};
}

function refusal(promise, code) {
	return assert.rejects(
		promise,
		(error) => error instanceof AdapterRefusal && error.code === code,
	);
}

test("exchange still accepts one complete response", async () => {
	const response = await exchange(() => child(), "{}", "/engine", 100);
	assert.equal(response.code, "OK");
});

test("asynchronous stdin failure is contained and reaped", async () => {
	const process = child({ stdinFailure: new Error("EPIPE") });
	await refusal(exchange(() => process, "request", "/engine", 100), "ENGINE_START_FAILED");
	assert.deepEqual(process.signals, ["SIGTERM"]);
});

test("timeout waits for close and escalates when TERM is ignored", async () => {
	const process = child({ ignoreTerm: true, never: true });
	const pending = exchange(() => process, "request", "/engine", 1);

	await new Promise((resolve) => setTimeout(resolve, TERMINATION_GRACE_MS / 2));
	assert.deepEqual(process.signals, ["SIGTERM"]);
	await refusal(pending, "ENGINE_TIMEOUT");
	assert.deepEqual(process.signals, ["SIGTERM", "SIGKILL"]);
});
