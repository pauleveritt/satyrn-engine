import { spawn } from "node:child_process";
import { relative, resolve } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * The adapter — makes the Python engine reachable inside Pi as
 * `/implement CONTRACT`. Starts the engine as a subprocess
 * (`uv run --project $SATYRN_ENGINE_REPO satyrn-engine protocol`), sends
 * one versioned JSON request on stdin, reads one JSON response, and
 * converts every transport failure into a named refusal. The engine's own
 * refusals pass through verbatim.
 *
 * Install the containing package with `pi install`, then set
 * `SATYRN_ENGINE_REPO` to the engine checkout. See docs/usage.md.
 */

export const PROTOCOL_VERSION = 1;
export const DEFAULT_DEADLINE_MS = 30_000;
export const TERMINATION_GRACE_MS = 100;
export const DELIVERY_TERMINATION_GRACE_MS = 8_000;
export const DEFAULT_ATTEMPT_TIMEOUT_SECONDS = 900;
export const DEFAULT_DELIVERY_DEADLINE_MS = (DEFAULT_ATTEMPT_TIMEOUT_SECONDS + 10) * 1000;
export const MAX_RECEIPT_BYTES = 64 * 1024;

export const ENGINE_REFUSAL_CODES = [
	"CONTRACT_UNREADABLE",
	"CONTRACT_INVALID_YAML",
	"CONTRACT_MISSING_FIELD",
	"REPO_UNAVAILABLE",
	"INVALID_REQUEST",
	"PATH_UNDECLARED",
	"REVISION_UNAVAILABLE",
	"REVISION_STALE",
	"ANCHOR_MISSING",
	"ANCHOR_AMBIGUOUS",
	"MUTATION_FAILED",
] as const;

export type EngineRefusalCode = (typeof ENGINE_REFUSAL_CODES)[number];

export type AdapterRefusalCode =
	| "ADAPTER_ERROR"
	| "ENGINE_CRASHED"
	| "ENGINE_MALFORMED_RESPONSE"
	| "ENGINE_START_FAILED"
	| "ENGINE_TIMEOUT"
	| "INVALID_REQUEST"
	| "MUTATION_CONTEXT_INVALID";

/** A named adapter refusal: a transport failure the engine never sees. */
export class AdapterRefusal extends Error {
	readonly code: AdapterRefusalCode;
	constructor(code: AdapterRefusalCode, message: string) {
		super(message);
		this.name = "AdapterRefusal";
		this.code = code;
	}
}

/**
 * The minimal child-process surface the adapter needs (the test seam).
 *
 * `close` (not `exit`) is the event the adapter listens for: Node fires
 * `exit` before the stdio streams drain, so reading stdout on `exit` can
 * lose trailing bytes. `close` fires after the process has ended AND the
 * stdio streams are closed.
 */
export interface SpawnedChild {
	stdin: {
		write(data: string): void;
		end(): void;
		on?(event: "error", cb: (err: Error) => void): void;
	};
	stdout: { on(event: "data", cb: (chunk: string) => void): void };
	stderr: { on(event: "data", cb: (chunk: string) => void): void };
	on(event: "close", cb: (code: number | null) => void): void;
	on(event: "error", cb: (err: Error) => void): void;
	kill(signal?: "SIGTERM" | "SIGKILL"): boolean | void;
}

export type Spawner = (
	command: string,
	args: readonly string[],
	options: { cwd?: string },
) => SpawnedChild;

interface EngineResponseBase {
	readonly version: 1;
	readonly message: string;
	readonly result?: unknown;
}

export interface EngineSuccessResponse extends EngineResponseBase {
	readonly ok: true;
	readonly code: "OK";
}

export interface EngineRefusalResponse extends EngineResponseBase {
	readonly ok: false;
	readonly code: EngineRefusalCode;
}

export type EngineResponse = EngineSuccessResponse | EngineRefusalResponse;

export function isEngineRefusalCode(value: unknown): value is EngineRefusalCode {
	return (
		typeof value === "string" &&
		(ENGINE_REFUSAL_CODES as readonly string[]).includes(value)
	);
}

export const DELIVERY_CODE_OUTCOMES = {
	OK: "candidate-created",
	CONTRACT_UNREADABLE: "refused",
	CONTRACT_INVALID_YAML: "refused",
	CONTRACT_MISSING_FIELD: "refused",
	REPO_UNAVAILABLE: "refused",
	REPO_NOT_GIT: "refused",
	REPO_DIRTY: "refused",
	INVALID_CANDIDATE_ID: "refused",
	CANDIDATE_EXISTS: "refused",
	COMMAND_UNAVAILABLE: "refused",
	COMMAND_TIMEOUT: "discarded",
	COMMAND_FAILED: "discarded",
	COMMAND_CHANGED_HEAD: "discarded",
	NO_CHANGES: "discarded",
	GIT_FAILED: "refused",
	CLEANUP_FAILED: "refused",
} as const;

export type DeliveryCode = keyof typeof DELIVERY_CODE_OUTCOMES;
export type DeliveryRefusalCode = Exclude<DeliveryCode, "OK">;
export type DeliveryRefusalOutcome =
	(typeof DELIVERY_CODE_OUTCOMES)[DeliveryRefusalCode];

interface DeliveryReceiptBase {
	readonly version: 1;
	readonly message: string;
	readonly contract_id: string | null;
	readonly repository: string;
	readonly base_commit: string | null;
	readonly command_exit: number | null;
	readonly worktree_path: string | null;
}

export interface CandidateCreatedReceipt extends DeliveryReceiptBase {
	readonly outcome: "candidate-created";
	readonly code: "OK";
	readonly candidate_ref: string;
	readonly candidate_commit: string;
	readonly changed_paths: readonly string[];
}

export interface DeliveryRefusalReceipt extends DeliveryReceiptBase {
	readonly outcome: DeliveryRefusalOutcome;
	readonly code: DeliveryRefusalCode;
	readonly candidate_ref: string | null;
	readonly candidate_commit: string | null;
	readonly changed_paths: readonly string[] | null;
}

export type DeliveryReceipt =
	| CandidateCreatedReceipt
	| DeliveryRefusalReceipt;

export interface DeliveryInvocation {
	readonly command: "uv";
	readonly args: readonly string[];
	readonly cwd: string;
}

export type DiagnosticSink = (chunk: string) => void;

/** Build the versioned JSON request the engine's `protocol` subcommand reads. */
export function buildRequest(repo: string, contract: string): string {
	return JSON.stringify({
		version: PROTOCOL_VERSION,
		operation: "check",
		repo,
		contract,
	});
}

/** Parse and shape-check one engine response; throws AdapterRefusal. */
export function parseResponse(text: string): EngineResponse {
	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response is not valid JSON");
	}
	if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response has an unexpected shape");
	}
	const body = parsed as Record<string, unknown>;
	if (
		body.version !== PROTOCOL_VERSION ||
		typeof body.ok !== "boolean" ||
		typeof body.code !== "string" ||
		typeof body.message !== "string"
	) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response has an unexpected shape");
	}
	const optionalResult = Object.hasOwn(body, "result") ? { result: body.result } : {};
	if (body.ok) {
		if (body.code !== "OK") {
			throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response has inconsistent status fields");
		}
		return {
			version: PROTOCOL_VERSION,
			ok: true,
			code: "OK",
			message: body.message,
			...optionalResult,
		};
	}
	if (!isEngineRefusalCode(body.code)) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response has an unknown refusal code");
	}
	return {
		version: PROTOCOL_VERSION,
		ok: false,
		code: body.code,
		message: body.message,
		...optionalResult,
	};
}

function isNullableString(value: unknown): value is string | null {
	return value === null || typeof value === "string";
}

function isDeliveryCode(value: unknown): value is DeliveryCode {
	return typeof value === "string" && Object.hasOwn(DELIVERY_CODE_OUTCOMES, value);
}

function isNullableStringArray(value: unknown): value is readonly string[] | null {
	return (
		value === null ||
		(Array.isArray(value) && value.every((path) => typeof path === "string"))
	);
}

function isNullableInteger(value: unknown): value is number | null {
	return value === null || (typeof value === "number" && Number.isInteger(value));
}

export function parseDeliveryReceipt(text: string): DeliveryReceipt {
	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "delivery receipt is not valid JSON");
	}
	if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "delivery receipt has an unexpected shape");
	}
	const body = parsed as Record<string, unknown>;
	if (
		body.version !== PROTOCOL_VERSION ||
		!isDeliveryCode(body.code) ||
		body.outcome !== DELIVERY_CODE_OUTCOMES[body.code] ||
		typeof body.message !== "string" ||
		!isNullableString(body.contract_id) ||
		typeof body.repository !== "string" ||
		!isNullableString(body.base_commit) ||
		!isNullableString(body.candidate_ref) ||
		!isNullableString(body.candidate_commit) ||
		!isNullableStringArray(body.changed_paths) ||
		!isNullableInteger(body.command_exit) ||
		!isNullableString(body.worktree_path)
	) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "delivery receipt has an unexpected shape");
	}
	const base: DeliveryReceiptBase = {
		version: PROTOCOL_VERSION,
		message: body.message,
		contract_id: body.contract_id,
		repository: body.repository,
		base_commit: body.base_commit,
		command_exit: body.command_exit,
		worktree_path: body.worktree_path,
	};
	if (body.code === "OK") {
		if (
			typeof body.candidate_ref !== "string" ||
			typeof body.candidate_commit !== "string" ||
			body.changed_paths === null
		) {
			throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "delivery receipt has inconsistent candidate fields");
		}
		return {
			...base,
			outcome: "candidate-created",
			code: "OK",
			candidate_ref: body.candidate_ref,
			candidate_commit: body.candidate_commit,
			changed_paths: body.changed_paths,
		};
	}
	return {
		...base,
		outcome: DELIVERY_CODE_OUTCOMES[body.code],
		code: body.code,
		candidate_ref: body.candidate_ref,
		candidate_commit: body.candidate_commit,
		changed_paths: body.changed_paths,
	};
}

export function buildDeliveryInvocation(
	repo: string,
	contract: string,
	model: string,
	engineRepo: string,
	timeoutSeconds: number = DEFAULT_ATTEMPT_TIMEOUT_SECONDS,
): DeliveryInvocation {
	const sourceRepo = resolve(repo);
	const resolvedEngineRepo = resolve(engineRepo);
	const resolvedContract = resolve(sourceRepo, contract);
	const relativeContract = relative(sourceRepo, resolvedContract);
	const innerContract =
		relativeContract !== "" && !relativeContract.startsWith("..")
			? relativeContract
			: resolvedContract;
	return {
		command: "uv",
		cwd: resolvedEngineRepo,
		args: [
			"run",
			"--project",
			resolvedEngineRepo,
			"satyrn-engine",
			"deliver",
			"--repo",
			sourceRepo,
			"--timeout",
			String(timeoutSeconds),
			resolvedContract,
			"--",
			"uv",
			"run",
			"--project",
			resolvedEngineRepo,
			"satyrn-engine",
			"attempt",
			`--model=${model}`,
			"--",
			innerContract,
		],
	};
}

/**
 * Run one request/response exchange against the engine. The JSON response
 * is authoritative; a nonzero exit with no parseable response is a crash;
 * the deadline is the adapter's own, because Pi imposes no host deadline.
 */
export async function exchange(
	spawner: Spawner,
	request: string,
	engineRepo: string,
	deadlineMs: number,
): Promise<EngineResponse> {
	return new Promise((resolvePromise, rejectPromise) => {
		let child: SpawnedChild;
		try {
			child = spawner("uv", ["run", "--project", engineRepo, "satyrn-engine", "protocol"], {
				cwd: engineRepo,
			});
		} catch (err) {
			rejectPromise(new AdapterRefusal("ENGINE_START_FAILED", `could not start the engine: ${String(err)}`));
			return;
		}

		let stdout = "";
		let settled = false;
		let pendingRefusal: AdapterRefusal | undefined;
		let terminationTimer: ReturnType<typeof setTimeout> | undefined;

		const requestTermination = (refusal: AdapterRefusal): void => {
			if (settled || pendingRefusal !== undefined) return;
			pendingRefusal = refusal;
			clearTimeout(deadlineTimer);
			try {
				child.kill("SIGTERM");
			} catch {
				// The close event remains authoritative. A failed TERM is followed by KILL.
			}
			if (settled) return;
			terminationTimer = setTimeout(() => {
				try {
					child.kill("SIGKILL");
				} catch {
					// Do not claim completion before close even when signaling fails.
				}
			}, TERMINATION_GRACE_MS);
		};

		const deadlineTimer = setTimeout(() => {
			requestTermination(
				new AdapterRefusal("ENGINE_TIMEOUT", `no response within ${deadlineMs} ms`),
			);
		}, deadlineMs);

		try {
			child.on("close", (code) => {
				if (settled) return;
				settled = true;
				clearTimeout(deadlineTimer);
				if (terminationTimer !== undefined) clearTimeout(terminationTimer);
				if (pendingRefusal !== undefined) {
					rejectPromise(pendingRefusal);
					return;
				}
				try {
					resolvePromise(parseResponse(stdout));
				} catch (refusal) {
					if (code !== 0) {
						rejectPromise(
							new AdapterRefusal("ENGINE_CRASHED", `engine exited ${code} with no valid response`),
						);
					} else {
						rejectPromise(refusal as AdapterRefusal);
					}
				}
			});
			child.on("error", (err) => {
				requestTermination(
					new AdapterRefusal("ENGINE_START_FAILED", `engine failed to start: ${err.message}`),
				);
			});
			child.stdout.on("data", (chunk) => {
				stdout += chunk;
			});
			child.stdin.on?.("error", (err) => {
				requestTermination(
					new AdapterRefusal("ENGINE_START_FAILED", `could not write the request: ${err.message}`),
				);
			});
			child.stdin.write(request);
			child.stdin.end();
		} catch (err) {
			requestTermination(
				new AdapterRefusal("ENGINE_START_FAILED", `could not write the request: ${String(err)}`),
			);
		}
	});
}

export async function runDelivery(
	spawner: Spawner,
	invocation: DeliveryInvocation,
	deadlineMs: number,
	diagnostic: DiagnosticSink = (chunk) => process.stderr.write(chunk),
	terminationGraceMs: number = DELIVERY_TERMINATION_GRACE_MS,
): Promise<DeliveryReceipt> {
	return new Promise((resolvePromise, rejectPromise) => {
		let child: SpawnedChild;
		try {
			child = spawner(invocation.command, invocation.args, { cwd: invocation.cwd });
		} catch (err) {
			rejectPromise(new AdapterRefusal("ENGINE_START_FAILED", `could not start delivery: ${String(err)}`));
			return;
		}

		let stdout = "";
		let oversized = false;
		let settled = false;
		let pendingRefusal: AdapterRefusal | undefined;
		let terminationTimer: ReturnType<typeof setTimeout> | undefined;

		const requestTermination = (refusal: AdapterRefusal): void => {
			if (settled || pendingRefusal !== undefined) return;
			pendingRefusal = refusal;
			clearTimeout(deadlineTimer);
			try {
				child.kill("SIGTERM");
			} catch {
				// The close event remains authoritative. A failed TERM is followed by KILL.
			}
			if (settled) return;
			terminationTimer = setTimeout(() => {
				try {
					child.kill("SIGKILL");
				} catch {
					// Never report completion while the child may still be running.
				}
			}, terminationGraceMs);
		};

		const deadlineTimer = setTimeout(() => {
			requestTermination(
				new AdapterRefusal("ENGINE_TIMEOUT", `delivery did not finish within ${deadlineMs} ms`),
			);
		}, deadlineMs);

		try {
			child.on("error", (err) => {
				requestTermination(
					new AdapterRefusal("ENGINE_START_FAILED", `delivery failed to start: ${err.message}`),
				);
			});
			child.on("close", (code) => {
				if (settled) return;
				settled = true;
				clearTimeout(deadlineTimer);
				if (terminationTimer !== undefined) clearTimeout(terminationTimer);
				if (pendingRefusal !== undefined) {
					rejectPromise(pendingRefusal);
					return;
				}
				if (oversized) {
					rejectPromise(new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "delivery receipt exceeds 65536 bytes"));
					return;
				}
				try {
					resolvePromise(parseDeliveryReceipt(stdout));
				} catch (refusal) {
					rejectPromise(
						code !== 0
							? new AdapterRefusal("ENGINE_CRASHED", `delivery exited ${code} with no valid receipt`)
							: (refusal as AdapterRefusal),
					);
				}
			});
			child.stdout.on("data", (chunk) => {
				if (Buffer.byteLength(stdout) + Buffer.byteLength(chunk) > MAX_RECEIPT_BYTES) {
					oversized = true;
					return;
				}
				stdout += chunk;
			});
			child.stderr.on("data", diagnostic);
			child.stdin.on?.("error", (err) => {
				requestTermination(
					new AdapterRefusal("ENGINE_START_FAILED", `could not close delivery stdin: ${err.message}`),
				);
			});
			child.stdin.end();
		} catch (err) {
			requestTermination(
				new AdapterRefusal("ENGINE_START_FAILED", `could not close delivery stdin: ${String(err)}`),
			);
		}
	});
}

/** The command surface, with the spawner and deadline injected (test seams). */
export function createAdapter(spawner: Spawner, deadlineMs: number = DEFAULT_DELIVERY_DEADLINE_MS) {
	return {
		async implement(
			args: string,
			ctx: { cwd: string; ui: { notify(message: string, level: "info" | "error"): void } },
		): Promise<void> {
			const engineRepo = process.env.SATYRN_ENGINE_REPO;
			const model = process.env.SATYRN_MODEL;
			if (!engineRepo) {
				ctx.ui.notify("satyrn-engine: ENGINE_START_FAILED: SATYRN_ENGINE_REPO is not set", "error");
				return;
			}
			if (!model) {
				ctx.ui.notify("satyrn-engine: ENGINE_START_FAILED: SATYRN_MODEL is not set", "error");
				return;
			}
			const contractArg = args.trim();
			if (!contractArg) {
				ctx.ui.notify("satyrn-engine: USAGE: expected a CONTRACT path", "error");
				return;
			}
			const invocation = buildDeliveryInvocation(ctx.cwd, contractArg, model, engineRepo);
			try {
				const receipt = await runDelivery(spawner, invocation, deadlineMs);
				if (receipt.code === "OK") {
					ctx.ui.notify(
						`satyrn-engine: OK: ${receipt.candidate_ref} ${receipt.candidate_commit}`,
						"info",
					);
				} else {
					ctx.ui.notify(`satyrn-engine: ${receipt.code}: ${receipt.message}`, "error");
				}
			} catch (err) {
				const refusal =
					err instanceof AdapterRefusal
						? err
						: new AdapterRefusal(
								"ADAPTER_ERROR",
								err instanceof Error ? err.message : String(err),
							);
				ctx.ui.notify(`satyrn-engine: ${refusal.code}: ${refusal.message}`, "error");
			}
		},
	};
}

export default function (pi: ExtensionAPI) {
	const adapter = createAdapter(spawn, DEFAULT_DELIVERY_DEADLINE_MS);
	pi.registerCommand("implement", {
		description: "Run one isolated model attempt and create or discard a candidate",
		handler: adapter.implement,
	});
}
