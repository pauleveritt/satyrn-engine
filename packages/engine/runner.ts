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

// **Correction, 2026-09-14 (Phase 1 Task 4).** Renamed `bash` to
// `self_test` (Ruling 1): native `bash` is now Pi's own built-in, restored
// alongside this tool rather than overridden by it, so the two names must
// not collide. The `command` parameter stays in the schema -- a model's
// prior still expects one on a tool it can call with arguments -- but it
// is now optional, described as ignored, and the schema is open
// (`additionalProperties: true`): a guessed argument is accepted at the
// schema layer rather than refused before any hook can observe the call.
// The contract's own declared command always runs regardless of what is
// supplied, exactly as before; `run_tests` also restores the carried set
// (`preserve`, `checks`, and tracked test infrastructure) from the
// mutation's accepted base immediately before every run, so edits to any
// of it never count.

const TestParameters = {
	type: "object",
	properties: {
		command: { type: "string", description: "ignored; the contract's own command always runs" },
	},
	additionalProperties: true,
} as const;

/**
 * The TS exchange deadline for `self_test`. `runner.py:39`
 * (`DEFAULT_TEST_TIMEOUT_SECONDS = 120.0`) owns the self-test bound; this
 * is only its ceiling -- the 120s the runner itself may spend plus the
 * `uv run satyrn-engine protocol` child's own start-up -- never a second
 * independent number (I4).
 */
export const SELF_TEST_DEADLINE_MS = 130_000;

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

export function buildTestRequest(context: MutationContext): string {
	return JSON.stringify({
		version: PROTOCOL_VERSION,
		operation: "test",
		repo: context.repo,
		contract: context.contract,
		command: null,
		base_commit: context.base_commit ?? null,
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

export function createRunner(context: MutationContext, exchangeRequest: ExchangeRequest): Runner {
	return {
		async execute(_toolCallId: string, _rawInput: unknown): Promise<RunnerToolResult> {
			// Ruling 1: the schema is open and `command` is ignored -- any
			// argument the model supplies is accepted, never inspected. The
			// contract's own declared command always runs.
			try {
				const request = buildTestRequest(context);
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
		name: "self_test",
		label: "Run the contract's self-test",
		// pi lists a tool under "Available tools" only when its registration
		// supplies this. `self_test` no longer shadows native `bash`, so pi's
		// "use bash for ls, rg, find" guideline does not apply here -- this
		// line only needs to say what the tool actually runs.
		promptSnippet:
			"runs the contract's declared self-test command (tests carried from the base are restored first) and " +
			"returns failed test ids; any argument is ignored",
		description:
			"Run the contract's declared self-test command. Tests carried from the accepted base -- preserve " +
			"paths, checks, and tracked test infrastructure -- are restored first, so edits to them never count. " +
			"Any argument supplied is ignored; the contract's own command always runs.",
		parameters: TestParameters,
		execute: runner.execute,
	});
	pi.on("tool_result", async (event) => {
		if (event.toolName !== "self_test" || !isRecord(event.details) || event.details.satyrn !== true) {
			return undefined;
		}
		return event.details.ok === true ? undefined : { isError: true };
	});
}

export default function runnerExtension(
	pi: ExtensionAPI,
	environment: MutationEnvironment = process.env,
	exchangeRequest?: ExchangeRequest,
	engineExchangeFactory: typeof createEngineExchange = createEngineExchange,
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
		exchangeRequest ?? engineExchangeFactory(spawn, engineRepo, SELF_TEST_DEADLINE_MS),
	);
}
