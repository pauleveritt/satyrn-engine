#!/usr/bin/env node

/**
 * Drive the adapter's transport behavior against a fake spawner.
 *
 * The Python tripwire cannot reach TypeScript, and Pi itself is not a
 * dependency here, so this harness instantiates the shipped
 * `orchestrator.ts` with an ExtensionAPI-shaped double and an injected
 * fake spawner. It tests the artifact contributors install: request
 * building, response parsing, the four transport conversions, and the
 * `/implement` command surface. No model, no network, no real engine.
 */

import { readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, resolve } from "node:path";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const extensionPath = resolve(root, "packages/engine/orchestrator.ts");
const {
	createAdapter,
	buildRequest,
	parseResponse,
	parseDeliveryReceipt,
	buildDeliveryInvocation,
	runDelivery,
	exchange,
	AdapterRefusal,
} = await import(
	pathToFileURL(extensionPath)
);

// The adapter reads this at call time; set it once so the command-surface
// cases below start in the "engine is reachable" state. Individual cases
// delete and restore it to exercise the unset path.
process.env.SATYRN_ENGINE_REPO = String(root);
process.env.SATYRN_MODEL = "fixture/model";

const FIXTURES = resolve(root, "tests/fixtures/protocol");

let failures = 0;
function ok(name, condition, detail = "") {
	if (condition) {
		console.log(`ok - ${name}`);
	} else {
		failures += 1;
		console.log(`FAIL - ${name}${detail ? `: ${detail}` : ""}`);
	}
}

/** A fake spawner child that behaves per case. */
function mockChild({ stdoutText = "", stderrText = "", exitCode = 0, emitError = null, neverExits = false }) {
	const listeners = { close: [], error: [] };
	let killed = false;
	let closed = false;
	const close = (code) => {
		if (closed) return;
		closed = true;
		for (const cb of listeners.close) cb(code);
	};
	const child = {
		stdin: {
			write() {},
			end() {
				queueMicrotask(() => {
					if (neverExits) return;
					close(exitCode);
				});
			},
			on() {},
		},
		stdout: {
			on(event, cb) {
				if (event === "data" && stdoutText) queueMicrotask(() => cb(stdoutText));
			},
		},
		stderr: {
			on(event, cb) {
				if (event === "data" && stderrText) queueMicrotask(() => cb(stderrText));
			},
		},
		on(event, cb) {
			if (listeners[event]) listeners[event].push(cb);
		},
		kill() {
			killed = true;
			queueMicrotask(() => close(null));
		},
		get killed() {
			return killed;
		},
	};
	if (emitError) queueMicrotask(() => { for (const cb of listeners.error) cb(emitError); });
	return child;
}

// --- Case 1: acceptance round trip through the real fixtures ----------------
const okRequest = await readFile(resolve(FIXTURES, "request-check-valid.json"), "utf8");
const okResponse = await readFile(resolve(FIXTURES, "response-check-ok.json"), "utf8");
const built = buildRequest("repo-dir", "contract.yaml");
ok("buildRequest produces the versioned shape", built === JSON.stringify({
	version: 1,
	operation: "check",
	repo: "repo-dir",
	contract: "contract.yaml",
}));

const parsedOk = parseResponse(okResponse);
ok("parseResponse accepts the ok fixture", parsedOk.ok === true && parsedOk.code === "OK");

// --- Case 2: engine refusal passes through verbatim --------------------------
const refusalText = await readFile(resolve(FIXTURES, "response-check-refusal-repo.json"), "utf8");
const parsedRefusal = parseResponse(refusalText);
ok(
	"parseResponse passes engine refusals through",
	parsedRefusal.ok === false && parsedRefusal.code === "REPO_UNAVAILABLE",
);

// --- Case 3: malformed response -> ENGINE_MALFORMED_RESPONSE -----------------
for (const bad of ["not json", "42", '{"version":2,"ok":true,"code":"OK","message":""}']) {
	try {
		parseResponse(bad);
		ok(`malformed response refused (${bad})`, false, "did not throw");
	} catch (err) {
		ok(
			`malformed response refused (${bad})`,
			err instanceof AdapterRefusal && err.code === "ENGINE_MALFORMED_RESPONSE",
			err.message,
		);
	}
}

// --- Case 4: exchange conversions ---------------------------------------------
const engineRepo = root;

async function exchangeCase(name, case_, deadlineMs) {
	const child = mockChild(case_);
	try {
		const response = await exchange(spawnerFor(child), okRequest, engineRepo, deadlineMs);
		ok(name, true, `resolved with ${response.code}`);
	} catch (err) {
		ok(name, false, `unexpected ${err.code ?? err.message}`);
	}
}

function spawnerFor(child) {
	return () => child;
}

await exchangeCase("acceptance exchange resolves", { stdoutText: okResponse, exitCode: 0 }, 500);
await exchangeCase("engine refusal exchange resolves", { stdoutText: refusalText, exitCode: 6 }, 500);

const crashed = await exchange(spawnerFor(mockChild({ stdoutText: "", exitCode: 1 })), okRequest, engineRepo, 500).catch((err) => err);
ok("crash -> ENGINE_CRASHED", crashed instanceof AdapterRefusal && crashed.code === "ENGINE_CRASHED", crashed.message);

const malformed = await exchange(spawnerFor(mockChild({ stdoutText: "not json", exitCode: 0 })), okRequest, engineRepo, 500).catch((err) => err);
ok("garbage on stdout -> ENGINE_MALFORMED_RESPONSE", malformed instanceof AdapterRefusal && malformed.code === "ENGINE_MALFORMED_RESPONSE", malformed.message);

const startFailed = await exchange(() => { throw new Error("ENOENT"); }, okRequest, engineRepo, 500).catch((err) => err);
ok("spawn throw -> ENGINE_START_FAILED", startFailed instanceof AdapterRefusal && startFailed.code === "ENGINE_START_FAILED", startFailed.message);

const timedOut = await exchange(spawnerFor(mockChild({ neverExits: true })), okRequest, engineRepo, 50).catch((err) => err);
ok("timeout -> ENGINE_TIMEOUT", timedOut instanceof AdapterRefusal && timedOut.code === "ENGINE_TIMEOUT", timedOut.message);

// --- Case 5: E5 delivery receipt and command ---------------------------------
const deliveryOk = await readFile(resolve(root, "tests/fixtures/delivery/receipt-ok.json"), "utf8");
const deliveryRefusal = await readFile(resolve(root, "tests/fixtures/delivery/receipt-repo-dirty.json"), "utf8");
const parsedDelivery = parseDeliveryReceipt(deliveryOk);
ok(
	"parseDeliveryReceipt accepts E3 success",
	parsedDelivery.code === "OK" && parsedDelivery.candidate_ref !== null,
);
const invocation = buildDeliveryInvocation(root, "contracts/task.yaml", "fixture/model", root, 12);
ok(
	"buildDeliveryInvocation nests E5 inside E3",
	invocation.args.includes("deliver") &&
		invocation.args.includes("attempt") &&
		invocation.args.includes("--model=fixture/model") &&
		invocation.args.includes("contracts/task.yaml"),
);
const diagnostics = [];
const delivered = await runDelivery(
	spawnerFor(mockChild({ stdoutText: deliveryOk, stderrText: "transcript\n" })),
	invocation,
	500,
	(chunk) => diagnostics.push(chunk),
);
ok("runDelivery returns receipt and drains diagnostics", delivered.code === "OK" && diagnostics.join("") === "transcript\n");

// --- Case 6: the /implement command surface -----------------------------------
// Each case clears `notifications` first so the assertion targets the one
// notify call that case made, not an accumulated index.
const notifications = [];
const fakeCtx = {
	cwd: root,
	ui: { notify(message, level) { notifications.push({ message, level }); } },
};

notifications.length = 0;
await createAdapter(() => mockChild({ stdoutText: deliveryOk, exitCode: 0 }), 500).implement(
	"tests/fixtures/contracts/valid.yaml",
	fakeCtx,
);
ok(
	"implement creates and reports a candidate",
	notifications.length === 1 && notifications[0].message.includes("refs/satyrn/candidates/greeting/head") && notifications[0].level === "info",
	JSON.stringify(notifications),
);

notifications.length = 0;
await createAdapter(() => mockChild({ stdoutText: deliveryRefusal, exitCode: 8 }), 500).implement("anything.yaml", fakeCtx);
ok(
	"implement surfaces engine refusals verbatim",
	notifications.length === 1 && notifications[0].message.startsWith("satyrn-engine: REPO_DIRTY") && notifications[0].level === "error",
	JSON.stringify(notifications),
);

delete process.env.SATYRN_ENGINE_REPO;
notifications.length = 0;
await createAdapter(() => mockChild({})).implement("x.yaml", fakeCtx);
ok(
	"implement refuses when SATYRN_ENGINE_REPO is unset",
	notifications.length === 1 && notifications[0].message.includes("SATYRN_ENGINE_REPO") && notifications[0].level === "error",
	JSON.stringify(notifications),
);

process.env.SATYRN_ENGINE_REPO = String(root);
delete process.env.SATYRN_MODEL;
notifications.length = 0;
await createAdapter(() => mockChild({})).implement("x.yaml", fakeCtx);
ok(
	"implement refuses when SATYRN_MODEL is unset",
	notifications.length === 1 && notifications[0].message.includes("SATYRN_MODEL") && notifications[0].level === "error",
	JSON.stringify(notifications),
);

process.env.SATYRN_MODEL = "fixture/model";
notifications.length = 0;
await createAdapter(() => mockChild({})).implement("   ", fakeCtx);
ok(
	"implement refuses an empty CONTRACT",
	notifications.length === 1 && notifications[0].message.includes("USAGE") && notifications[0].level === "error",
	JSON.stringify(notifications),
);

if (failures > 0) {
	console.error(`\n${failures} failure(s)`);
	process.exit(1);
}
console.log("\nall adapter replay cases passed");
