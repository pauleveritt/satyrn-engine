import { spawn } from "node:child_process";
import { resolve } from "node:path";
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

/** A named adapter refusal: a transport failure the engine never sees. */
export class AdapterRefusal extends Error {
	readonly code: string;
	constructor(code: string, message: string) {
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
	stdin: { write(data: string): void; end(): void };
	stdout: { on(event: "data", cb: (chunk: string) => void): void };
	on(event: "close", cb: (code: number | null) => void): void;
	on(event: "error", cb: (err: Error) => void): void;
	kill(): void;
}

export type Spawner = (
	command: string,
	args: readonly string[],
	options: { cwd?: string },
) => SpawnedChild;

export interface EngineResponse {
	version: number;
	ok: boolean;
	code: string;
	message: string;
}

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
	const body = parsed as Record<string, unknown>;
	if (
		body.version !== PROTOCOL_VERSION ||
		typeof body.ok !== "boolean" ||
		typeof body.code !== "string" ||
		typeof body.message !== "string"
	) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "engine response has an unexpected shape");
	}
	return body as unknown as EngineResponse;
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
		const timer = setTimeout(() => {
			child.kill();
			settled = true;
			rejectPromise(new AdapterRefusal("ENGINE_TIMEOUT", `no response within ${deadlineMs} ms`));
		}, deadlineMs);

		child.stdout.on("data", (chunk) => {
			stdout += chunk;
		});

		child.on("error", (err) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
			rejectPromise(new AdapterRefusal("ENGINE_START_FAILED", `engine failed to start: ${err.message}`));
		});

		child.on("close", (code) => {
			if (settled) return;
			settled = true;
			clearTimeout(timer);
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

		try {
			child.stdin.write(request);
			child.stdin.end();
		} catch (err) {
			settled = true;
			clearTimeout(timer);
			rejectPromise(new AdapterRefusal("ENGINE_START_FAILED", `could not write the request: ${String(err)}`));
		}
	});
}

/** The command surface, with the spawner and deadline injected (test seams). */
export function createAdapter(spawner: Spawner, deadlineMs: number = DEFAULT_DEADLINE_MS) {
	return {
		async implement(
			args: string,
			ctx: { cwd: string; ui: { notify(message: string, level: "info" | "error"): void } },
		): Promise<void> {
			const engineRepo = process.env.SATYRN_ENGINE_REPO;
			if (!engineRepo) {
				ctx.ui.notify("satyrn-engine: ENGINE_START_FAILED: SATYRN_ENGINE_REPO is not set", "error");
				return;
			}
			const contractArg = args.trim();
			if (!contractArg) {
				ctx.ui.notify("satyrn-engine: USAGE: expected a CONTRACT path", "error");
				return;
			}
			const repo = ctx.cwd;
			const contract = resolve(repo, contractArg);
			const request = buildRequest(repo, contract);
			try {
				const response = await exchange(spawner, request, engineRepo, deadlineMs);
				if (response.ok) {
					ctx.ui.notify("satyrn-engine: OK", "info");
				} else {
					ctx.ui.notify(`satyrn-engine: ${response.code}: ${response.message}`, "error");
				}
			} catch (err) {
				const refusal = err as AdapterRefusal;
				ctx.ui.notify(`satyrn-engine: ${refusal.code}: ${refusal.message}`, "error");
			}
		},
	};
}

export default function (pi: ExtensionAPI) {
	const adapter = createAdapter(spawn, DEFAULT_DEADLINE_MS);
	pi.registerCommand("implement", {
		description: "Run the satyrn engine on a contract (accept or named refusal)",
		handler: adapter.implement,
	});
}
