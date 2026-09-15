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

/**
 * Guard 2, enforced (Phase 3b). The route proof's three Engine cells never
 * called `self_test` although the prompt named it and every bash result
 * repeated it (`bounds.ts`); Baseline admission cells ran pytest through
 * bash 0-16 times per self-hosted cell. A bash command that only runs the
 * repository's test runner is answered by `self_test` instead: the command
 * becomes `true`, and its result is the self-test's compact result under
 * one sentence saying so. A command that does anything else as well runs
 * untouched -- the Engine never drops work the model asked for.
 */
export const REDIRECTED_COMMAND = "true";

const RUNNER_PROGRAMS = new Set(["pytest", "py.test"]);
const PYTHON = /^python(?:3(?:\.\d+)?)?$/;
/** Segments that may accompany a test run anywhere: they change nothing a test reads. */
const ALONGSIDE = new Set(["cd", "echo", "printf", "true"]);
/** Output filters, allowed only on the receiving side of a pipe. */
const FILTERS = new Set(["tail", "head", "grep", "egrep"]);
/** `uv run` options that take a separate value. */
const UV_RUN_VALUE_FLAGS = new Set([
	"--with", "--with-requirements", "--project", "--directory", "--python", "-p", "--group",
	"--extra", "--package", "--env-file", "--index",
]);

interface Segment {
	readonly words: string[];
	readonly piped: boolean;
}

/**
 * Split a bash command into simple commands, quote-aware. Returns null for
 * anything this reading cannot vouch for: command substitution (other than
 * `$(pwd)`), backticks, subshells, input redirection, a background `&`, an
 * output redirection to anything but `/dev/null` or another descriptor, or
 * an unclosed quote. Null means "not a pure test run".
 */
export function shellSegments(command: string): Segment[] | null {
	if (command.replaceAll("$(pwd)", "").includes("$(") || command.includes("`")) return null;
	const segments: { words: string[]; piped: boolean }[] = [{ words: [], piped: false }];
	let word = "";
	let inWord = false;
	let quote: "'" | '"' | null = null;
	const endWord = () => {
		if (inWord) segments[segments.length - 1].words.push(word);
		word = "";
		inWord = false;
	};
	const next = (piped: boolean) => {
		endWord();
		segments.push({ words: [], piped });
	};
	for (let i = 0; i < command.length; i++) {
		const c = command[i];
		if (quote === "'") {
			if (c === "'") quote = null;
			else word += c;
			continue;
		}
		if (quote === '"') {
			if (c === '"') quote = null;
			else if (c === "\\" && i + 1 < command.length) word += command[++i];
			else word += c;
			continue;
		}
		if (c === "'" || c === '"') {
			quote = c;
			inWord = true;
		} else if (c === "\\") {
			if (command[i + 1] === "\n") i++;
			else if (i + 1 < command.length) {
				word += command[++i];
				inWord = true;
			}
		} else if (c === " " || c === "\t") {
			endWord();
		} else if (c === "\n" || c === ";") {
			next(false);
		} else if (c === "|") {
			if (command[i + 1] === "|") {
				i++;
				next(false);
			} else next(true);
		} else if (c === "&" && command[i + 1] === "&") {
			i++;
			next(false);
		} else if (c === ">" || (c === "&" && command[i + 1] === ">")) {
			if (inWord && !/^\d+$/.test(word)) return null;
			word = "";
			inWord = false;
			let j = c === "&" ? i + 2 : i + 1;
			if (command[j] === ">") j++;
			if (command[j] === "&") {
				j++;
				const start = j;
				while (j < command.length && /\d/.test(command[j])) j++;
				if (j === start) return null;
			} else {
				while (command[j] === " " || command[j] === "\t") j++;
				const start = j;
				while (j < command.length && !" \t\n;|&".includes(command[j])) j++;
				if (command.slice(start, j) !== "/dev/null") return null;
			}
			i = j - 1;
		} else if (c === "&" || c === "<" || c === "(" || c === ")") {
			return null;
		} else {
			word += c;
			inWord = true;
		}
	}
	if (quote !== null) return null;
	endWord();
	return segments.filter((segment) => segment.words.length > 0);
}

function basename(word: string): string {
	return word.slice(word.lastIndexOf("/") + 1);
}

/** Leading `NAME=value` assignments, `env`, and a `timeout [flags] DURATION` wrapper. */
function unwrap(words: readonly string[]): string[] {
	let rest = [...words];
	while (rest.length > 0 && (rest[0] === "env" || /^[A-Za-z_][A-Za-z0-9_]*=/.test(rest[0]))) rest = rest.slice(1);
	if (rest[0] === "timeout") {
		rest = rest.slice(1);
		while (rest.length > 0 && rest[0].startsWith("-")) rest = rest.slice(1);
		if (rest.length === 0 || !/^\d+(?:\.\d+)?[smhd]?$/.test(rest[0])) return [];
		rest = rest.slice(1);
	}
	return rest;
}

/** `pytest`, `py.test`, `python[3[.N]] -m pytest`, each optionally under `uv run [options]`. */
export function isTestRunner(words: readonly string[]): boolean {
	let rest = unwrap(words);
	if (rest.length >= 2 && basename(rest[0]) === "uv" && rest[1] === "run") {
		rest = rest.slice(2);
		while (rest.length > 0 && rest[0].startsWith("-")) {
			const flag = rest[0];
			rest = rest.slice(UV_RUN_VALUE_FLAGS.has(flag) ? 2 : 1);
		}
		rest = unwrap(rest);
	}
	if (rest.length === 0) return false;
	if (RUNNER_PROGRAMS.has(basename(rest[0]))) return true;
	return PYTHON.test(basename(rest[0])) && rest[1] === "-m" && rest[2] === "pytest";
}

/** True only for a command whose every segment is a test run, a harmless companion, or a filter after a pipe. */
export function isTestRunCommand(command: string): boolean {
	const segments = shellSegments(command);
	if (segments === null) return false;
	let runs = false;
	for (const { words, piped } of segments) {
		if (isTestRunner(words)) runs = true;
		else if (!ALONGSIDE.has(words[0]) && !(piped && FILTERS.has(words[0]))) return false;
	}
	return runs;
}

export function redirectSentence(testCommand: readonly string[]): string {
	return `The Engine ran self_test in place of this command: it runs "${testCommand.join(" ")}" over the whole suite, whatever paths or flags the command named.`;
}

/**
 * The completion gate (Phase 3b). When the model's turn ends with no tool
 * call -- Pi is about to stop -- and no self-test has completed through the
 * Engine since the last landed `edit` or `write` (or none ever ran), the
 * Engine runs `self_test` itself, once per mutation generation. A failing or
 * timed-out run goes back to the model as one follow-up message; a pass, or
 * a run the engine refuses, ends the session as it would have.
 */
export function enforcedMessage(resultText: string): string {
	return `Before you finish: the Engine ran self_test because nothing had run it since the last change, and it did not pass.\n${resultText}`;
}

/** An assistant message that ends the agent loop: no tool call, and not an error or an abort. */
export function isFinalTurn(message: unknown): boolean {
	if (!isRecord(message) || message.role !== "assistant") return false;
	if (message.stopReason === "error" || message.stopReason === "aborted") return false;
	const content = Array.isArray(message.content) ? message.content : [];
	return !content.some((part) => isRecord(part) && part.type === "toolCall");
}

export function registerRunner(pi: ExtensionAPI, context: MutationContext, exchangeRequest: ExchangeRequest): void {
	const runner = createRunner(context, exchangeRequest);
	const redirected = new Set<string>();
	// A generation counts landed mutations; `checked` is the generation the
	// last completed self-test ran against (null: none has).
	let generation = 0;
	let checked: number | null = null;
	const run = async (toolCallId: string): Promise<RunnerToolResult> => {
		const at = generation;
		const result = await runner.execute(toolCallId, {});
		if (result.details.ok) checked = at;
		return result;
	};
	const note = async (kind: string, data: Record<string, unknown>): Promise<void> => {
		try {
			await pi.appendEntry(kind, data);
		} catch {
			// Evidence, not permission.
		}
	};
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
		execute: (toolCallId: string) => run(toolCallId),
	});
	pi.on("tool_call", async (event) => {
		// Pi: `event.input` is mutable and later handlers and the tool see the
		// mutation (core/extensions/types.d.ts, ToolCallEvent).
		if (event.toolName !== "bash" || !isRecord(event.input) || typeof event.input.command !== "string") return undefined;
		if (!isTestRunCommand(event.input.command)) return undefined;
		event.input.command = REDIRECTED_COMMAND;
		redirected.add(event.toolCallId);
		await note("self_test_redirected", { toolCallId: event.toolCallId });
		return undefined;
	});
	pi.on("tool_result", async (event) => {
		if (event.toolName === "edit" && isRecord(event.details) && event.details.satyrn === true && event.details.ok === true) {
			generation += 1;
			return undefined;
		}
		if (event.toolName === "write" && event.isError !== true) {
			generation += 1;
			return undefined;
		}
		if (event.toolName === "bash" && redirected.delete(event.toolCallId)) {
			const result = await run(event.toolCallId);
			return {
				content: [{ type: "text", text: `${redirectSentence(context.test_command)}\n${result.content[0].text}` }],
				isError: result.details.ok !== true,
			};
		}
		if (event.toolName !== "self_test" || !isRecord(event.details) || event.details.satyrn !== true) {
			return undefined;
		}
		return event.details.ok === true ? undefined : { isError: true };
	});
	pi.on("turn_end", async (event) => {
		// Pi awaits turn_end listeners before it polls its steering and
		// follow-up queues (pi-agent-core agent-loop.js runLoop), so a
		// follow-up queued here continues the same run: one agent_end. A
		// custom message, not `sendUserMessage`: that path emits
		// `queue_update` events into the --mode json stream
		// (agent-session.js _queueFollowUp), and the custom type names the
		// Engine as the message's author; Pi hands it to the model as a
		// user message (core/messages.js convertToLlm).
		if (!isFinalTurn(event.message) || checked === generation) return;
		const at = generation;
		const result = await runner.execute("self_test_enforced", {});
		checked = at;
		const details = result.details;
		const passed = details.ok && details.result.exit_code === 0 && !details.result.timed_out;
		const followUp = details.ok && !passed;
		await note("self_test_enforced", {
			generation: at,
			code: details.code,
			exit_code: details.ok ? details.result.exit_code : null,
			follow_up: followUp,
		});
		if (followUp) {
			pi.sendMessage(
				{ customType: "self_test_enforced", content: enforcedMessage(result.content[0].text), display: true, details: undefined },
				{ deliverAs: "followUp" },
			);
		}
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
