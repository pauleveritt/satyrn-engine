import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import {
	createEngineExchange,
	MUTATION_CONTEXT_ENV,
	parseMutationContext,
	type ExchangeRequest,
	type MutationContext,
	type MutationEnvironment,
} from "./mutator.ts";
import {
	AdapterRefusal,
	PROTOCOL_VERSION,
	isEngineRefusalCode,
	type AdapterRefusalCode,
	type EngineRefusalCode,
	type EngineResponse,
} from "./orchestrator.ts";

/**
 * The E7 `bash` tool. `satyrn-evals` V13d found that restricting the
 * surface to `read,edit` cost 8 of 12 successes on a repair task relative
 * to a `bash`-carrying baseline (one-sided Fisher p = 0.00067): baseline's
 * `bash` calls were overwhelmingly `pytest`, run to read the failure and
 * edit. This tool restores exactly that one capability, and nothing else.
 * See docs/superpowers/specs/2026-09-06-e7-model-invocable-test-runner-design.md.
 *
 * **Correction, 2026-09-06:** the original tool was named `run_tests` and
 * took no parameters -- the contract's command ran verbatim, with no
 * model-supplied argument, because an argument the model could choose is a
 * shell by another name. A smoke of four uncounted cells found it was
 * never invoked: a model's prior for a tool named `bash` expects a
 * `command` argument, and a closed empty schema does not match that
 * prior. Renamed to `bash`, with a required `command` string in the
 * schema so the model's prior is satisfied there. The restriction did not
 * move to the model -- it moved into `execute`/the Python core, which
 * compares the supplied `command` against the contract's `test_command`
 * and only ever runs the contract's own argv. Restricting at the schema
 * instead would fail invisibly: pi's schema validation runs before every
 * extension hook, so a call rejected there never reaches `execute` and is
 * unobservable by any hook.
 */

// **Correction, 2026-09-07.** The paragraph above attributes the
// zero-invocation smoke to the model declining a closed empty schema.
// That inference was wrong and is kept rather than edited away: `--tools`
// gates extension-registered tools as well as pi's built-ins, so with
// `read,edit` this tool answered "Tool bash not found" for every call and
// was never reachable under any name. The argument for taking a `command`
// argument still stands on its own -- a closed schema refuses invisibly,
// since pi validates before `beforeToolCall` -- but it was not what the
// smoke measured. See `attempt.py`'s `--tools` comment.

const TestParameters = {
	type: "object",
	properties: {
		command: { type: "string", minLength: 1 },
	},
	required: ["command"],
	additionalProperties: false,
} as const;

export interface TestResult {
	readonly exit_code: number;
	readonly output: string;
	readonly truncated: boolean;
	readonly timed_out: boolean;
}

export interface TestSuccessResponse {
	readonly version: 1;
	readonly ok: true;
	readonly code: "OK";
	readonly message: string;
	readonly result: TestResult;
}

export interface TestRefusalResponse {
	readonly version: 1;
	readonly ok: false;
	readonly code: EngineRefusalCode;
	readonly message: string;
	readonly result: null;
}

export type TestResponse = TestSuccessResponse | TestRefusalResponse;

export type RunnerToolRefusalCode = AdapterRefusalCode | EngineRefusalCode;

export interface RunnerToolSuccessDetails {
	readonly satyrn: true;
	readonly ok: true;
	readonly code: "OK";
	readonly result: TestResult;
}

export interface RunnerToolRefusalDetails {
	readonly satyrn: true;
	readonly ok: false;
	readonly code: RunnerToolRefusalCode;
	readonly result: null;
}

export type RunnerToolDetails = RunnerToolSuccessDetails | RunnerToolRefusalDetails;

export interface RunnerToolResult {
	readonly content: [{ readonly type: "text"; readonly text: string }];
	readonly details: RunnerToolDetails;
}

export interface Runner {
	execute(toolCallId: string, input: unknown): Promise<RunnerToolResult>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function buildTestRequest(context: MutationContext, command: string): string {
	return JSON.stringify({
		version: PROTOCOL_VERSION,
		operation: "test",
		repo: context.repo,
		contract: context.contract,
		command,
	});
}

export function parseTestResponse(response: EngineResponse): TestResponse {
	if (response.ok) {
		if (
			response.code !== "OK" ||
			!isRecord(response.result) ||
			typeof response.result.exit_code !== "number" ||
			!Number.isInteger(response.result.exit_code) ||
			typeof response.result.output !== "string" ||
			typeof response.result.truncated !== "boolean" ||
			typeof response.result.timed_out !== "boolean"
		) {
			throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "successful test response has an unexpected shape");
		}
		return {
			version: PROTOCOL_VERSION,
			ok: true,
			code: "OK",
			message: response.message,
			result: {
				exit_code: response.result.exit_code,
				output: response.result.output,
				truncated: response.result.truncated,
				timed_out: response.result.timed_out,
			},
		};
	}
	if (
		!isEngineRefusalCode(response.code) ||
		(response.result !== null &&
			!(response.code === "INVALID_REQUEST" && response.result === undefined))
	) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "refused test response must have a null result");
	}
	return {
		version: PROTOCOL_VERSION,
		ok: false,
		code: response.code,
		message: response.message,
		result: null,
	};
}

function successResult(result: TestResult): RunnerToolResult {
	const summary = result.timed_out
		? `Test command timed out; exit_code=${result.exit_code}`
		: `Test command exited ${result.exit_code}`;
	return {
		content: [{ type: "text", text: `${summary}\n${result.output}` }],
		details: { satyrn: true, ok: true, code: "OK", result },
	};
}

function refusalResult(code: RunnerToolRefusalCode, message: string): RunnerToolResult {
	return {
		content: [{ type: "text", text: `${code}: ${message}` }],
		details: { satyrn: true, ok: false, code, result: null },
	};
}

function readCommand(rawInput: unknown): string {
	if (!isRecord(rawInput) || typeof rawInput.command !== "string" || rawInput.command.length === 0) {
		throw new AdapterRefusal("INVALID_REQUEST", "bash tool call requires a non-empty 'command' string");
	}
	return rawInput.command;
}

export function createRunner(context: MutationContext, exchangeRequest: ExchangeRequest): Runner {
	return {
		async execute(_toolCallId: string, rawInput: unknown): Promise<RunnerToolResult> {
			try {
				const command = readCommand(rawInput);
				const request = buildTestRequest(context, command);
				const response = parseTestResponse(await exchangeRequest(request));
				if (!response.ok || response.result === null) {
					return refusalResult(response.code, response.message);
				}
				return successResult(response.result);
			} catch (error) {
				const refusal = error instanceof AdapterRefusal ? error : undefined;
				return refusalResult(
					refusal?.code ?? "ADAPTER_ERROR",
					refusal?.message ?? (error instanceof Error ? error.message : String(error)),
				);
			}
		},
	};
}

export function registerRunner(pi: ExtensionAPI, context: MutationContext, exchangeRequest: ExchangeRequest): void {
	const runner = createRunner(context, exchangeRequest);
	pi.registerTool({
		name: "bash",
		label: "Run the contract's test command",
		description:
			"Run a shell command. Only the contract's exact declared test command is ever executed; any other " +
			"command is refused with a message naming the one command that is allowed, verbatim.",
		parameters: TestParameters,
		execute: runner.execute,
	});
	pi.on("tool_result", async (event) => {
		if (event.toolName !== "bash" || !isRecord(event.details) || event.details.satyrn !== true) {
			return undefined;
		}
		return event.details.ok === true ? undefined : { isError: true };
	});
}

export default function runnerExtension(
	pi: ExtensionAPI,
	environment: MutationEnvironment = process.env,
	exchangeRequest?: ExchangeRequest,
): void {
	const contextText = environment[MUTATION_CONTEXT_ENV];
	const engineRepo = environment.SATYRN_ENGINE_REPO;
	if (contextText === undefined || engineRepo === undefined) return;
	let context: MutationContext;
	try {
		context = parseMutationContext(contextText);
	} catch {
		return;
	}
	registerRunner(
		pi,
		context,
		exchangeRequest ?? createEngineExchange(spawn, engineRepo),
	);
}
