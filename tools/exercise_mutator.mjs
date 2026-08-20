#!/usr/bin/env node

import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
	DEFAULT_DEADLINE_MS,
	exchange,
} from "../packages/engine/orchestrator.ts";
import {
	createMutator,
	parseMutationContext,
} from "../packages/engine/mutator.ts";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

function usage(stream) {
	stream.write(
		"usage: node --experimental-strip-types tools/exercise_mutator.mjs CONTEXT.json INPUT.json\n",
	);
}

export async function main(arguments_, output = process.stdout, error = process.stderr) {
	if (arguments_.length !== 2) {
		usage(error);
		return 2;
	}
	try {
		const [contextPath, inputPath] = arguments_.map((path) => resolve(path));
		const context = parseMutationContext(await readFile(contextPath, "utf8"));
		const input = JSON.parse(await readFile(inputPath, "utf8"));
		const mutator = createMutator(
			context,
			(request) => exchange(spawn, request, root, DEFAULT_DEADLINE_MS),
		);
		const result = await mutator.execute("fixture", input);
		output.write(`${JSON.stringify(result)}\n`);
		return 0;
	} catch (failure) {
		const message = failure instanceof Error ? failure.message : String(failure);
		error.write(`exercise_mutator: ${message}\n`);
		return 1;
	}
}

const invokedPath = process.argv[1] === undefined ? "" : resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) {
	process.exitCode = await main(process.argv.slice(2));
}
