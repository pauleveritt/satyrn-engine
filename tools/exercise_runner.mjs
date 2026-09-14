#!/usr/bin/env node

import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
	exchange,
} from "../packages/engine/orchestrator.ts";
import {
	createRunner,
	SELF_TEST_DEADLINE_MS,
} from "../packages/engine/runner.ts";
import { parseMutationContext } from "../packages/engine/mutator.ts";

const root = dirname(dirname(fileURLToPath(import.meta.url)));

function usage(stream) {
	stream.write(
		"usage: node --experimental-strip-types tools/exercise_runner.mjs CONTEXT.json\n",
	);
}

export async function main(arguments_, output = process.stdout, error = process.stderr) {
	if (arguments_.length !== 1) {
		usage(error);
		return 2;
	}
	try {
		const [contextPath] = [arguments_[0]].map((path) => resolve(path));
		const context = parseMutationContext(await readFile(contextPath, "utf8"));
		const runner = createRunner(
			context,
			(request) => exchange(spawn, request, root, SELF_TEST_DEADLINE_MS),
		);
		const result = await runner.execute("fixture", {});
		output.write(`${JSON.stringify(result)}\n`);
		return 0;
	} catch (failure) {
		const message = failure instanceof Error ? failure.message : String(failure);
		error.write(`exercise_runner: ${message}\n`);
		return 1;
	}
}

const invokedPath = process.argv[1] === undefined ? "" : resolve(process.argv[1]);
if (invokedPath === fileURLToPath(import.meta.url)) {
	process.exitCode = await main(process.argv.slice(2));
}
