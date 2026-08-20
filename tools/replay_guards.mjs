#!/usr/bin/env node

import { readFile, readdir } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const fixtureDirectory = resolve(root, "tests/fixtures/guards");
const extensionUrl = pathToFileURL(resolve(root, "packages/engine/engine.ts"));

function isRecord(value) {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

function nonnegativeInteger(value) {
	return Number.isInteger(value) && value >= 0;
}

export function parseFixture(text, path) {
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
	if (!Array.isArray(value.calls)) throw new Error(`${path}: calls must be an array`);
	for (const [index, call] of value.calls.entries()) {
		if (!isRecord(call) || typeof call.toolName !== "string" || !("input" in call)) {
			throw new Error(`${path}: calls[${index}] must contain toolName and input`);
		}
	}
	if (!isRecord(value.expected)) throw new Error(`${path}: expected must be an object`);
	if (!nonnegativeInteger(value.expected.blocked)) {
		throw new Error(`${path}: expected.blocked must be a non-negative integer`);
	}
	if (!nonnegativeInteger(value.expected.entries)) {
		throw new Error(`${path}: expected.entries must be a non-negative integer`);
	}
	if (
		"firstBlock" in value.expected &&
		value.expected.firstBlock !== null &&
		(!Number.isInteger(value.expected.firstBlock) || value.expected.firstBlock < 1)
	) {
		throw new Error(`${path}: expected.firstBlock must be null or a positive integer`);
	}
	return value;
}

export async function replayFixture(registerExtension, fixture) {
	let handler;
	const entries = [];
	registerExtension({
		on(event, candidate) {
			if (event === "tool_call") handler = candidate;
		},
		appendEntry(kind, data) {
			entries.push({ kind, data });
		},
	});
	if (typeof handler !== "function") {
		throw new Error(`${fixture.name}: extension did not register tool_call`);
	}

	let blocked = 0;
	let firstBlock = null;
	for (const [index, call] of fixture.calls.entries()) {
		if ((await handler(call))?.block === true) {
			blocked += 1;
			firstBlock ??= index + 1;
		}
	}
	return {
		name: fixture.name,
		calls: fixture.calls.length,
		blocked,
		firstBlock,
		entries: entries.length,
	};
}

function verify(fixture, observed) {
	const mismatches = [];
	for (const field of ["blocked", "entries"]) {
		if (observed[field] !== fixture.expected[field]) {
			mismatches.push(`${field} ${observed[field]} != ${fixture.expected[field]}`);
		}
	}
	if (
		"firstBlock" in fixture.expected &&
		observed.firstBlock !== fixture.expected.firstBlock
	) {
		mismatches.push(
			`firstBlock ${observed.firstBlock} != ${fixture.expected.firstBlock}`,
		);
	}
	if (mismatches.length > 0) throw new Error(`${fixture.name}: ${mismatches.join("; ")}`);
}

async function defaultFixturePaths() {
	return (await readdir(fixtureDirectory))
		.filter((name) => name.endsWith(".json"))
		.sort()
		.map((name) => resolve(fixtureDirectory, name));
}

function usage(stream) {
	stream.write(
		"usage: node --experimental-strip-types tools/replay_guards.mjs [FIXTURE ...]\n",
	);
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
		const { default: registerExtension } = await import(extensionUrl);
		const paths = arguments_.length === 0 ? await defaultFixturePaths() : arguments_;
		if (paths.length === 0) throw new Error("no guard fixtures found");
		for (const path of paths) {
			const absolutePath = resolve(path);
			const fixture = parseFixture(await readFile(absolutePath, "utf8"), absolutePath);
			const observed = await replayFixture(registerExtension, fixture);
			verify(fixture, observed);
			output.write(`${JSON.stringify(observed)}\n`);
		}
		return 0;
	} catch (failure) {
		const message = failure instanceof Error ? failure.message : String(failure);
		error.write(`replay_guards: ${message}\n`);
		return 1;
	}
}

const invokedPath = process.argv[1] === undefined ? "" : resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) {
	process.exitCode = await main(process.argv.slice(2));
}
