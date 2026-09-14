#!/usr/bin/env node
// Replays a recorded Pi event stream against one guard extension, with a
// fake Pi (`on`/`registerTool`/`appendEntry` recorded, nothing more) and a
// fake engine exchange -- so no `uv run satyrn-engine protocol` is ever
// spawned under `just gates` (M5). See tests/*-brief.md's fixture shape for
// the event and expectation vocabulary this understands: `tool_call`,
// `tool_result`, `tool_exec`, and `expectedEntries`.

import { readFile, readdir } from "node:fs/promises";
import assert from "node:assert/strict";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const fixtureDirectory = resolve(root, "tests/fixtures/events");
const engineDirectory = resolve(root, "packages/engine");

function isRecord(value) {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

function deepEqual(actual, expected) {
	try {
		assert.deepStrictEqual(actual, expected);
		return true;
	} catch {
		return false;
	}
}

function isSubset(actual, expectedData) {
	if (!isRecord(actual)) return false;
	return Object.entries(expectedData).every(([key, value]) => deepEqual(actual[key], value));
}

/** A fake `pi` recording every `on`/`registerTool`/`appendEntry` call. */
function fakePi() {
	const handlers = {};
	const tools = {};
	const entries = [];
	return {
		pi: {
			on(event, handler) {
				(handlers[event] ??= []).push(handler);
			},
			registerTool(tool) {
				tools[tool.name] = tool;
			},
			async appendEntry(kind, data) {
				entries.push({ kind, data });
			},
		},
		handlers,
		tools,
		entries,
	};
}

/** Resolves `{version:1, ok:true, code:"OK", message:"", result:{...}}` from
 * whatever `path` the request named, with a fixed digest -- exactly enough
 * shape for a guard fixture that never inspects the engine's real reply. */
async function fakeExchange(request) {
	const parsed = JSON.parse(request);
	return {
		version: 1,
		ok: true,
		code: "OK",
		message: "",
		result: { path: parsed.path, sha256: "1".repeat(64), region: "" },
	};
}

async function firstDefined(handlers, event) {
	for (const handler of handlers ?? []) {
		const outcome = await handler(event);
		if (outcome !== undefined) return outcome;
	}
	return undefined;
}

async function replayToolCall(handlers, event) {
	const call = { toolCallId: event.toolCallId, toolName: event.toolName, input: event.input };
	const decision = await firstDefined(handlers, call);
	const expect = event.expect ?? {};
	const problems = [];
	const blocked = decision?.block === true;
	if (typeof expect.blocked === "boolean" && blocked !== expect.blocked) {
		problems.push(`expect.blocked ${expect.blocked} != ${blocked}`);
	}
	if (typeof expect.reasonContains === "string") {
		if (typeof decision?.reason !== "string" || !decision.reason.includes(expect.reasonContains)) {
			problems.push(`expect.reasonContains ${JSON.stringify(expect.reasonContains)} not found in ${JSON.stringify(decision?.reason)}`);
		}
	}
	if ("input" in expect && !deepEqual(call.input, expect.input)) {
		problems.push(`expect.input ${JSON.stringify(expect.input)} != ${JSON.stringify(call.input)}`);
	}
	return problems;
}

async function replayToolResult(handlers, event) {
	const call = { ...event };
	delete call.type;
	delete call.expect;
	const patch = await firstDefined(handlers, call);
	const expect = event.expect ?? {};
	const problems = [];
	if (expect.patched === false && patch !== undefined) {
		problems.push(`expect.patched false but a patch was returned: ${JSON.stringify(patch)}`);
	}
	if (typeof expect.contentEndsWith === "string") {
		const blocks = patch?.content;
		const lastText = Array.isArray(blocks) && blocks.length > 0 ? blocks.at(-1)?.text : undefined;
		if (typeof lastText !== "string" || !lastText.endsWith(expect.contentEndsWith)) {
			problems.push(`expect.contentEndsWith ${JSON.stringify(expect.contentEndsWith)} not found at the end of ${JSON.stringify(lastText)}`);
		}
	}
	return problems;
}

async function replayToolExec(tools, event) {
	const expect = event.expect ?? {};
	const problems = [];
	const tool = tools[event.toolName];
	if (tool === undefined) {
		problems.push(`no tool named ${event.toolName} is registered`);
		return problems;
	}
	const outcome = await tool.execute(event.toolCallId, event.input);
	if (typeof expect.code === "string" && outcome?.details?.code !== expect.code) {
		problems.push(`expect.code ${expect.code} != ${outcome?.details?.code}`);
	}
	return problems;
}

function checkExpectedHandlers(fixture, handlers) {
	const problems = [];
	for (const [event, count] of Object.entries(fixture.expectedHandlers ?? {})) {
		const actual = (handlers[event] ?? []).length;
		if (actual !== count) problems.push(`expectedHandlers.${event} ${count} != ${actual}`);
	}
	return problems;
}

function checkExpectedEntries(fixture, entries) {
	const expected = fixture.expectedEntries ?? [];
	const problems = [];
	if (entries.length !== expected.length) {
		problems.push(`expectedEntries.length ${expected.length} != ${entries.length}`);
		return problems;
	}
	for (const [index, expectedEntry] of expected.entries()) {
		const actualEntry = entries[index];
		if (actualEntry.kind !== expectedEntry.kind) {
			problems.push(`expectedEntries[${index}].kind ${expectedEntry.kind} != ${actualEntry.kind}`);
			continue;
		}
		if (!isSubset(actualEntry.data, expectedEntry.data ?? {})) {
			problems.push(`expectedEntries[${index}].data ${JSON.stringify(expectedEntry.data)} not a subset of ${JSON.stringify(actualEntry.data)}`);
		}
	}
	return problems;
}

export async function replayFixture(fixture) {
	const extensionUrl = pathToFileURL(resolve(engineDirectory, fixture.extension));
	const { default: registerExtension } = await import(extensionUrl);
	const { pi, handlers, tools, entries } = fakePi();
	const environment =
		fixture.context === null
			? {}
			: { SATYRN_MUTATION_CONTEXT: JSON.stringify(fixture.context), SATYRN_ENGINE_REPO: "/engine" };

	registerExtension(pi, environment, fakeExchange);

	const problems = [...checkExpectedHandlers(fixture, handlers)];
	for (const event of fixture.events ?? []) {
		switch (event.type) {
			case "tool_call":
				problems.push(...(await replayToolCall(handlers.tool_call, event)));
				break;
			case "tool_result":
				problems.push(...(await replayToolResult(handlers.tool_result, event)));
				break;
			case "tool_exec":
				problems.push(...(await replayToolExec(tools, event)));
				break;
			default:
				problems.push(`unknown event type ${event.type}`);
		}
	}
	problems.push(...checkExpectedEntries(fixture, entries));

	return {
		name: fixture.name,
		events: (fixture.events ?? []).length,
		entries: entries.length,
		problems,
	};
}

function parseFixture(text, path) {
	let value;
	try {
		value = JSON.parse(text);
	} catch (error) {
		throw new Error(`${path}: invalid JSON: ${error.message}`);
	}
	if (!isRecord(value)) throw new Error(`${path}: fixture must be an object`);
	if (typeof value.name !== "string" || value.name.length === 0) {
		throw new Error(`${path}: name must be a non-empty string`);
	}
	if (typeof value.extension !== "string" || value.extension.length === 0) {
		throw new Error(`${path}: extension must be a non-empty string`);
	}
	if (!("context" in value)) throw new Error(`${path}: context is required (an object, or null)`);
	if (!Array.isArray(value.events)) throw new Error(`${path}: events must be an array`);
	return value;
}

async function defaultFixturePaths() {
	return (await readdir(fixtureDirectory))
		.filter((name) => name.endsWith(".json"))
		.sort()
		.map((name) => resolve(fixtureDirectory, name));
}

function usage(stream) {
	stream.write("usage: node --experimental-strip-types tools/replay_events.mjs [FIXTURE ...]\n");
}

export async function main(arguments_, output = process.stdout, error = process.stderr) {
	if (arguments_.includes("--help")) {
		usage(output);
		return 0;
	}
	if (arguments_.some((argument) => argument.startsWith("-"))) {
		usage(error);
		return 2;
	}

	try {
		const paths = arguments_.length === 0 ? await defaultFixturePaths() : arguments_;
		if (paths.length === 0) throw new Error("no event fixtures found");
		for (const path of paths) {
			const absolutePath = resolve(path);
			const fixture = parseFixture(await readFile(absolutePath, "utf8"), absolutePath);
			const observed = await replayFixture(fixture);
			if (observed.problems.length > 0) {
				throw new Error(`${fixture.name}: ${observed.problems.join("; ")}`);
			}
			const { problems, ...summary } = observed;
			output.write(`${JSON.stringify(summary)}\n`);
		}
		return 0;
	} catch (failure) {
		const message = failure instanceof Error ? failure.message : String(failure);
		error.write(`replay_events: ${message}\n`);
		return 1;
	}
}

const invokedPath = process.argv[1] === undefined ? "" : resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) {
	process.exitCode = await main(process.argv.slice(2));
}
