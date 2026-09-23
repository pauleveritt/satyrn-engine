import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import { fenceNote } from "./bounds.ts";
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
	readonly compact_bytes: number;
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
			typeof response.result.timed_out !== "boolean" ||
			typeof response.result.compact_bytes !== "number"
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
				compact_bytes: response.result.compact_bytes,
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
 * Guard 2, enforced (Phase 3b; re-ruled 2026-09-18). The route proof's three
 * Engine cells never called `self_test` although the prompt named it, so
 * Phase 3b rewrote a bash command whose every segment was a pytest run. That
 * rule missed real runs hidden in compound commands, heredocs and wrapper
 * scripts, so the finish-on-green steer's trigger never fired. The Engine no
 * longer parses shell: it leaves the command alone and detects pytest's
 * summary line in the tool output, then -- when a source mutation has landed
 * since the last self-test -- runs its own self_test once and appends the
 * compact result under one sentence saying so. A green result with a pending
 * source generation fires the steer as before.
 */
export const DETECTED_SENTENCE =
	"The Engine also ran self_test on the current tree: this output carried a test run, and no self-test had run since the last change.";

const PYTEST_COUNT = /\b\d+ (?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warning|warnings|rerun|reruns)\b/;
const PYTEST_DURATION = /\bin \d+(?:\.\d+)?s\b/;

/** True when any line carries both a numeric pytest count and a duration. */
export function hasPytestSummary(output: string): boolean {
	for (const line of output.split(/\r?\n/)) {
		if (PYTEST_COUNT.test(line) && PYTEST_DURATION.test(line)) return true;
	}
	return false;
}

/** The text of every `text` content part of a tool result event. */
function resultText(event: unknown): string {
	if (!isRecord(event) || !Array.isArray(event.content)) return "";
	const parts: string[] = [];
	for (const part of event.content) {
		if (isRecord(part) && part.type === "text" && typeof part.text === "string") parts.push(part.text);
	}
	return parts.join("\n");
}

/**
 * The completion gate (Phase 3b). When the model's turn ends with no tool
 * call -- Pi is about to stop -- and no self-test has completed through the
 * Engine since the last landed `edit` or `write` (or none ever ran), the
 * Engine runs `self_test` itself, once per mutation generation. A failing or
 * timed-out run goes back to the model as one follow-up message; a pass, or
 * a run the engine refuses, ends the session as it would have.
 *
 * This gate only asks whether a self-test *ran* since the last mutation,
 * never whether it *passed* -- see `redStopMessage` below for the sibling
 * gate that covers a turn where this one is already satisfied but the last
 * completed run was red.
 */
export function enforcedMessage(resultText: string): string {
	return `Before you finish: the Engine ran self_test because nothing had run it since the last change, and it did not pass.\n${resultText}`;
}

/**
 * The red-stop gate (2026-09-23 plan, revised after review). The completion
 * gate above only asks whether a self-test has run since the last landed
 * mutation (`checked !== generation`); it never asks whether that run
 * *passed*. A model that lands a mutation, runs `self_test` itself (or has
 * it detected from a bash pytest run), sees it fail, and then stops on a
 * tool-call-free turn satisfies the completion gate -- `checked ===
 * generation` -- and gets no message at all. Diagnosis: satyrn-evals
 * development record
 * `records/2026-09-23-spike-mellum-class-review-script-n6.json`, Engine cell
 * `selfhost-review-script-20260923-130353-154738`.
 *
 * **Revision 1.** The first version reused the stored result instead of
 * re-running. Review found two ways that goes stale: a refused enforced run
 * advances `checked` without updating the last-run state, so a later
 * generation can report an earlier generation's failure; and a fix made
 * outside the tracked mutation tools (a bash `sed`/`git checkout`) changes
 * the tree without advancing `generation`, so "nothing has changed" can be
 * false. This gate now re-runs `self_test` through the same exchange as the
 * enforced branch and acts on that fresh result -- the cost is one full-suite
 * run, only in the red-stop case.
 */
export function redStopMessage(resultText: string): string {
	return `Before you finish: your last self_test did not pass, so the Engine ran it again on the current tree, and it still does not pass.\n${resultText}`;
}

/**
 * Component A, finish-on-green (design §2). Ten census cells held a
 * hidden-suite pass inside the 32k/48 line and none stopped there: they ran
 * coverage and lint gates the task never asked for, repaired their own
 * scaffolding, deleted their own tests to reach a count, and in four cells
 * regressed the tree. When a self-test run inside a turn exits 0 and at
 * least one *source* mutation has landed since the last green, the Engine
 * steers once. A nudge, never a hard stop: release one's cell 511653 had
 * its own suite green with the hidden suite at 19 of 20 and fixed the last
 * case five turns later.
 *
 * The text is the design's, byte for byte, and names no test path (plan
 * Ruling 1): the prompt already lists the carried set, and a path list
 * built from the contract would put the developer's real test filenames
 * into a model-visible message.
 */
export const FINISH_STEER =
	"self_test passes on the current tree. If the requested change is complete, stop now and " +
	"report what you changed. Do not commit, add provenance rows, run the full repository suite, " +
	"run linters, type checkers or `just` recipes, remove unused code, tidy or refactor, do a " +
	"final check, or change your tests to match a count; the developer reviews the candidate and " +
	"does those. Editing a passing tree can only break it. If something in the request is still " +
	"missing, say which part and continue.";

/** A path whose mutation is not a source mutation (plan Ruling 2). The
 * basename patterns require the `.py` extension -- `test_harness.rs` or
 * `test_data.json` outside a `tests/` directory is source, not test, and
 * must still arm the steer (controller Ruling A, fix round 1). */
export function isTestPath(path: string): boolean {
	const segments = path.split("/");
	if (segments.slice(0, -1).includes("tests")) return true;
	const name = segments[segments.length - 1];
	return name === "conftest.py" || (name.startsWith("test_") && name.endsWith(".py")) || name.endsWith("_test.py");
}

/** An assistant message that ends the agent loop: no tool call, and not an error or an abort. */
export function isFinalTurn(message: unknown): boolean {
	if (!isRecord(message) || message.role !== "assistant") return false;
	if (message.stopReason === "error" || message.stopReason === "aborted") return false;
	const content = Array.isArray(message.content) ? message.content : [];
	return !content.some((part) => isRecord(part) && part.type === "toolCall");
}

/**
 * Component B, runaway resume (design §3). Eight of 33 build cells over two
 * census nights ended on one 16,000-token design think with no tool call,
 * each opening on a single detail the cell decided to reason out instead of
 * act on. The completion gate does not catch them: on a length-cut turn
 * before any mutation the public suite is green, so the gate lets the
 * session end, which is what happened in all eight.
 */
export const RESUME_MESSAGE =
	"Your last turn hit the per-turn output cap with no tool call, so nothing was done. Do not " +
	"restate the plan. Make the next concrete change with a tool call: read the one file you " +
	"need, or edit.";

/** Two resumes bound the cost at two more turns (design §3). */
export const MAX_RESUMES = 2;

/** An assistant turn cut by the per-turn output cap with no tool call. */
export function isLengthCut(message: unknown): boolean {
	if (!isRecord(message) || message.role !== "assistant") return false;
	if (message.stopReason !== "length") return false;
	const content = Array.isArray(message.content) ? message.content : [];
	return !content.some((part) => isRecord(part) && part.type === "toolCall");
}

/** The turn's output token count, when Pi's usage block carries one. */
function outputTokens(message: unknown): number | null {
	if (!isRecord(message) || !isRecord(message.usage)) return null;
	const output = message.usage.output;
	return typeof output === "number" ? output : null;
}

export function registerRunner(pi: ExtensionAPI, context: MutationContext, exchangeRequest: ExchangeRequest): void {
	const runner = createRunner(context, exchangeRequest);
	// A generation counts landed mutations; `checked` is the generation the
	// last completed self-test ran against (null: none has).
	let generation = 0;
	let checked: number | null = null;
	// Red-stop gate (Revision 1): whether the last completed self-test
	// (model-called, detected, or enforced) passed, and the generation it
	// ran at. Both are set together on a successful exchange (`details.ok`);
	// a refused exchange -- model, detected, or enforced -- nulls
	// `lastPassed` instead of leaving a stale value from an earlier
	// generation in place (the bug Revision 1 fixes). `lastAt === null`
	// means no run has completed yet.
	let lastPassed: boolean | null = null;
	let lastAt: number | null = null;
	// The generation the red-stop gate, or the enforced branch's own failure
	// follow-up, last told the model about. `null`: never.
	let redStopped: number | null = null;
	// A second generation counting only source mutations (plan Ruling 2):
	// the completion gate keeps using `generation`, which counts every
	// landed mutation, while the steer must not re-arm when a cell rewrites
	// its own tests -- which is exactly what 453263 and 470484 did after
	// their green. `nudged` is the source generation the last steer went
	// out for (null: none has).
	let sourceGeneration = 0;
	let nudged: number | null = null;
	// Component B (design §3): counts resumes sent this session, capped at
	// MAX_RESUMES.
	let resumes = 0;
	const run = async (toolCallId: string): Promise<RunnerToolResult> => {
		const at = generation;
		const result = await runner.execute(toolCallId, {});
		if (result.details.ok) {
			checked = at;
			lastPassed = result.details.result.exit_code === 0 && !result.details.result.timed_out;
			lastAt = at;
		} else {
			// Revision 1: a refused exchange nulls `lastPassed` rather than
			// leaving an earlier generation's value in place.
			lastPassed = null;
		}
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
	/** One steer per source generation, on a green that happened inside a
	 * turn. Never on the enforced route (plan Ruling 3): that gate only runs
	 * when the model was already stopping. */
	const maybeSteer = async (details: RunnerToolDetails): Promise<void> => {
		if (!details.ok || details.result.exit_code !== 0 || details.result.timed_out) return;
		if (sourceGeneration === 0 || nudged === sourceGeneration) return;
		nudged = sourceGeneration;
		await note("finish_nudged", { generation: sourceGeneration });
		pi.sendMessage(
			{ customType: "finish_nudged", content: FINISH_STEER, display: true, details: undefined },
			{ deliverAs: "steer" },
		);
	};
	pi.on("tool_result", async (event) => {
		if (event.toolName === "bash") {
			const original = resultText(event);
			// No mutation has landed, or the Engine already tested this
			// generation: the detection must not run on a fresh session
			// (plan Task 6: output without a mutation generation must not fire).
			if (generation === 0 || checked === generation || !hasPytestSummary(original)) return undefined;
			const result = await run(event.toolCallId);
			await maybeSteer(result.details);
			await note("self_test_detected", { generation });
			return {
				content: [{ type: "text", text: `${original}${fenceNote(DETECTED_SENTENCE)}\n${result.content[0].text}` }],
				isError: (isRecord(event) && event.isError === true) || result.details.ok !== true,
			};
		}
		if (event.toolName === "edit" && isRecord(event.details) && event.details.satyrn === true && event.details.ok === true) {
			generation += 1;
			const path = isRecord(event.input) && typeof event.input.path === "string" ? event.input.path : null;
			if (path !== null && !isTestPath(path)) sourceGeneration += 1;
			return undefined;
		}
		if (event.toolName === "write" && event.isError !== true) {
			generation += 1;
			const path = isRecord(event.input) && typeof event.input.path === "string" ? event.input.path : null;
			if (path !== null && !isTestPath(path)) sourceGeneration += 1;
			return undefined;
		}
		if (event.toolName !== "self_test" || !isRecord(event.details) || event.details.satyrn !== true) {
			return undefined;
		}
		await maybeSteer(event.details as RunnerToolDetails);
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
		let gated = false;
		let enforcedRan = false;
		if (isFinalTurn(event.message) && checked !== generation) {
			enforcedRan = true;
			const at = generation;
			const result = await runner.execute("self_test_enforced", {});
			checked = at;
			const details = result.details;
			const passed = details.ok && details.result.exit_code === 0 && !details.result.timed_out;
			const followUp = details.ok && !passed;
			if (details.ok) {
				lastPassed = passed;
				lastAt = at;
			} else {
				// Revision 1: a refused enforced run nulls `lastPassed` too --
				// this used to leave an earlier generation's value in place
				// while `checked` (below) still advanced, which is exactly the
				// staleness Revision 1 fixes.
				lastPassed = null;
			}
			await note("self_test_enforced", {
				generation: at,
				code: details.code,
				exit_code: details.ok ? details.result.exit_code : null,
				follow_up: followUp,
			});
			if (followUp) {
				gated = true;
				// Plan Ruling 5: this follow-up tells the model its tree is
				// red exactly as the red-stop gate would, so it counts the
				// same way -- a model told once and stopping again on the
				// same tree is not told a second time.
				redStopped = at;
				pi.sendMessage(
					{ customType: "self_test_enforced", content: enforcedMessage(result.content[0].text), display: true, details: undefined },
					{ deliverAs: "followUp" },
				);
			}
		}
		// Red-stop gate (plan 2026-09-23, Revision 1), only when the enforced
		// branch above did not already run this turn: the last self-test to
		// complete, at the current mutation generation, did not pass. Revision
		// 1: re-run rather than reuse the stored result -- a refused enforced
		// run could otherwise leave `checked` advanced past a stale
		// `lastPassed`, and a fix made outside the tracked mutation tools (a
		// bash `sed`/`git checkout`) changes the tree without advancing
		// `generation`, so a stored result can go stale either way. `lastAt`,
		// not `checked`, gates this: `checked` also advances on a refused
		// enforced run, but `lastAt` only advances alongside `lastPassed`, on
		// a completed run.
		if (
			!enforcedRan &&
			isFinalTurn(event.message) &&
			lastPassed === false &&
			lastAt === generation &&
			redStopped !== generation
		) {
			const at = generation;
			const result = await runner.execute("self_test_red_stop", {});
			checked = at;
			const details = result.details;
			const passed = details.ok && details.result.exit_code === 0 && !details.result.timed_out;
			const followUp = details.ok && !passed;
			if (details.ok) {
				lastPassed = passed;
				lastAt = at;
			} else {
				lastPassed = null;
			}
			// Told about this generation either way: a pass means there is
			// nothing to tell, and a refusal is not a confirmed red tree.
			redStopped = generation;
			await note("self_test_red_stop", {
				generation: at,
				code: details.code,
				exit_code: details.ok ? details.result.exit_code : null,
				follow_up: followUp,
			});
			if (followUp) {
				gated = true;
				pi.sendMessage(
					{ customType: "self_test_red_stop", content: redStopMessage(result.content[0].text), display: true, details: undefined },
					{ deliverAs: "followUp" },
				);
			}
		}
		// Component B, runaway resume (design §3, Ruling 4): the gate goes
		// first and suppresses the resume for this turn. Two Engine messages
		// in one turn read as contradiction, and the gate's "your tree is
		// red, here is why" gets a tool call as surely as the resume would.
		// A runaway before any mutation leaves the public suite green, so the
		// gate is silent and the resume is the only message -- the eight
		// cells' exact shape.
		if (gated || !isLengthCut(event.message) || resumes >= MAX_RESUMES) return;
		resumes += 1;
		await note("runaway_resumed", { resume: resumes, output_tokens: outputTokens(event.message) });
		pi.sendMessage(
			{ customType: "runaway_resumed", content: RESUME_MESSAGE, display: true, details: undefined },
			{ deliverAs: "followUp" },
		);
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
