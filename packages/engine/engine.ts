import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Calls retained by the loop breaker. */
export const WINDOW = 20;

/** Matching admitted calls allowed before the next one is refused. */
export const THRESHOLD = 5;

/** Consecutive blocked calls allowed before Pi ends the current turn. */
export const CONSECUTIVE_BLOCK_LIMIT = 3;

export type JsonValue =
	| null
	| boolean
	| number
	| string
	| readonly JsonValue[]
	| { readonly [key: string]: JsonValue };

export interface ToolCall {
	readonly toolName: string;
	readonly input: unknown;
}

export interface LoopBrokenData {
	readonly tool: string;
	readonly repeats: number;
	readonly blockedSoFar: number;
}

export interface BlockDecision {
	readonly block: true;
	readonly reason: string;
	readonly entry: {
		readonly kind: "loop_broken";
		readonly data: LoopBrokenData;
	};
}

export interface LoopBreaker {
	inspect(call: ToolCall): BlockDecision | undefined;
	/**
	 * Report a landed edit: `path` now holds the bytes digested by `sha256`.
	 * That digest becomes `path`'s current revision, which is part of the key
	 * of every later call that reads `path` or the tree it belongs to.
	 */
	noteChange(path: string, sha256: string): void;
}

function canonicalJson(value: unknown, ancestors: WeakSet<object>): JsonValue | undefined {
	if (value === null) return null;

	switch (typeof value) {
		case "boolean":
		case "string":
			return value;
		case "number":
			return Number.isFinite(value) ? value : undefined;
		case "object":
			break;
		default:
			return undefined;
	}

	if (ancestors.has(value)) return undefined;
	ancestors.add(value);
	try {
		if (Array.isArray(value)) {
			const canonical: JsonValue[] = [];
			for (const item of value) {
				const normalized = canonicalJson(item, ancestors);
				if (normalized === undefined) return undefined;
				canonical.push(normalized);
			}
			return canonical;
		}

		const prototype = Object.getPrototypeOf(value);
		if (prototype !== Object.prototype && prototype !== null) return undefined;
		const canonicalEntries: [string, JsonValue][] = [];
		for (const key of Object.keys(value).sort()) {
			const normalized = canonicalJson(
				(value as Record<string, unknown>)[key],
				ancestors,
			);
			if (normalized === undefined) return undefined;
			canonicalEntries.push([key, normalized]);
		}
		return Object.fromEntries(canonicalEntries);
	} finally {
		ancestors.delete(value);
	}
}

/** The revision component of a path this attempt has not yet landed an edit on. */
const NO_REVISION = "unedited";

/**
 * The part of the workspace this call's result can depend on, as a key
 * component.
 *
 * A call naming a `path` reads that path, so only that path's revision can
 * change what it returns. A call naming no path -- the declared test command,
 * a build, a `git diff` -- can be changed by an edit anywhere, so it carries
 * the whole revision map. The map is serialized in sorted key order rather
 * than hashed: the component only has to compare equal for equal maps, and a
 * plain serialization keeps the breaker free of a crypto dependency.
 */
function workspacePart(input: JsonValue, revisions: ReadonlyMap<string, string>): string {
	if (input !== null && typeof input === "object" && !Array.isArray(input)) {
		const path = (input as { readonly [key: string]: JsonValue }).path;
		if (typeof path === "string") return revisions.get(path) ?? NO_REVISION;
	}
	const entries = [...revisions.entries()].sort(([left], [right]) => (left < right ? -1 : 1));
	return JSON.stringify(entries);
}

function callKey(call: ToolCall, revisions: ReadonlyMap<string, string>): string | undefined {
	const input = canonicalJson(call.input, new WeakSet());
	if (input === undefined) return undefined;
	return JSON.stringify([call.toolName, input, workspacePart(input, revisions)]);
}

export function createLoopBreaker(): LoopBreaker {
	const admitted: string[] = [];
	const blockedByKey = new Map<string, number>();
	const revisions = new Map<string, string>();

	return {
		inspect(call: ToolCall): BlockDecision | undefined {
			let key: string | undefined;
			try {
				key = callKey(call, revisions);
			} catch {
				return undefined;
			}
			if (key === undefined) return undefined;

			const repeats = admitted.reduce(
				(count, admittedKey) => count + Number(admittedKey === key),
				0,
			);
			if (repeats >= THRESHOLD) {
				const blockedSoFar = (blockedByKey.get(key) ?? 0) + 1;
				blockedByKey.set(key, blockedSoFar);
				return {
					block: true,
					reason:
						`This exact ${call.toolName} call already appeared ${repeats} times ` +
						`in the last ${WINDOW} admitted tool calls. Running it again will not ` +
						"change the result. Use what you already know and take a different concrete action.",
					entry: {
						kind: "loop_broken",
						data: { tool: call.toolName, repeats, blockedSoFar },
					},
				};
			}

			admitted.push(key);
			if (admitted.length > WINDOW) {
				const evicted = admitted.shift();
				if (evicted !== undefined && !admitted.includes(evicted)) {
					blockedByKey.delete(evicted);
				}
			}
			return undefined;
		},

		noteChange(path: string, sha256: string): void {
			// A landed edit changed the bytes under the model, so `path` now holds
			// a new revision. Every call keyed on that path -- and every path-less
			// call, which carries the whole revision map -- gets a key it has not
			// been seen at before, so repeating one now is verification rather than
			// cycling: it can return something new. Nothing is forgotten. The
			// window and the blocked counts stand; only the keys move.
			//
			// Keying rather than clearing is what keeps this from weakening the
			// bound elsewhere. Clearing had to be global, because the calls this
			// defect strands include exactly the ones no path can be attributed to
			// -- `pytest -q`, `git diff`, a build command -- so a landed edit to one
			// path also retired repeats of reads of an untouched path: forty reads
			// of an unchanged file admitted, none refused, while another file kept
			// changing. With the revision in the key, an untouched path's revision
			// does not move, its read key does not move, and those reads are refused
			// at THRESHOLD again -- five admitted, thirty-five refused over the same
			// forty. Whole-tree calls still go free after any landed edit, which is
			// the point: any landed edit really can change what they print.
			//
			// Churn stays bounded because revisions RECUR. Landing is not progress:
			// a landed edit only proves the anchor was real, unique and not yet
			// applied, and alternating edits satisfy that forever. Toggling a line
			// into and out of app.py (the v14a cell-009 shape) returns the file to a
			// digest it has already held, so the read of it reproduces a key the
			// window has already counted and accumulates to THRESHOLD like any other
			// repeat, and consecutive refusals still terminate the turn. Revisions
			// are held per path rather than as one composite fingerprint of the
			// tree, so N files each toggling between two contents cannot manufacture
			// 2^N distinct workspaces to spend on reads; a path-less call does
			// compare whole maps, but under churn those maps recur too.
			//
			// What this does NOT do, stated plainly. Unbounded new bytes are free: a
			// model appending one fresh line per edit produces a novel revision
			// every time, so the read of that file and every path-less call carry a
			// novel key every time, without bound. No state-keyed rule can close
			// that, because a novel state is progress by the only definition the
			// engine can check. This rule restores the strictness the window had
			// before staleness was considered -- no more than that.
			//
			// Nor did that baseline strictness stop the shape it is measured
			// against. In v14a cell-009 the breaker refused ten reads and the
			// attempt still ran to COMMAND_TIMEOUT at 900s across 146 calls, because
			// the toggling *edit* keys recurred every three to five calls and
			// WINDOW=20 never accumulated THRESHOLD of them. That is evidence this
			// breaker did not stop that sequence. It is not evidence that no rule
			// could: a count ceiling is not a diagnosis of unproductive work, and
			// would truncate legitimate repair too.
			//
			// Scope: revisions are learned only on the Satyrn mutator's route.
			// registerMutator runs only under SATYRN_MUTATION_CONTEXT; in an
			// ordinary Pi session `edit` is Pi's builtin, emits no details.satyrn,
			// and nothing here fires. The false refusal persists there unchanged.
			//
			// Refused edits (NO_CHANGE_REQUESTED, ANCHOR_MISSING,
			// ANCHOR_ALREADY_APPLIED) never reach here at all.
			revisions.set(path, sha256);
		},
	};
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null;
}

/** The post-edit digest shape `mutator.ts` guarantees on a successful edit. */
const SHA256 = /^[0-9a-f]{64}$/;

/**
 * **Retired 2026-09-09: the progress rule is not shipped.**
 *
 * A cycle measured that, at the moment of a breaker refusal, the cell's
 * eventual pass-rate is 0.09 when no edit has yet been accepted, against 0.74
 * and 0.42 once one or more have. That separation is real and the measurement
 * stands; the rule built on it does not, for three reasons found in review:
 *
 * 1. **Its margin is not what it appeared.** `CONSECUTIVE_BLOCK_LIMIT` already
 *    ends most of what it would end -- 22 of the 26 produced-nothing cells
 *    finished two calls later anyway. The incremental saving was 4 cells and
 *    162 calls, 149 of them a single timed-out cell, against 5 passes doing 65
 *    calls of real repair.
 * 2. **It never fired on the breaker it shipped in.** The only batches on this
 *    breaker recorded zero refusals, so every supporting number came from
 *    earlier ones.
 * 3. **Its counter could not mean what its name said.** `acceptedEdits`
 *    recognised only Satyrn-mutator edits, so with the flag on in a plain Pi
 *    session -- where edits never route through the mutator -- *every* refusal
 *    would terminate the turn.
 *
 * Off by default prevented ordinary exposure; it did not make the rule useful,
 * and an exposed switch invites enabling. The experiment is preserved in
 * `tests/test_loop_breaker.mjs`, which pins the behaviour this rule would have
 * changed, so reintroducing it fails a named row rather than being
 * rediscovered.
 */
export default function registerLoopBreaker(pi: ExtensionAPI): void {
	const breaker = createLoopBreaker();
	let consecutiveBlocks = 0;
	pi.on("tool_call", async (event) => {
		let decision: BlockDecision | undefined;
		try {
			decision = breaker.inspect({ toolName: event.toolName, input: event.input });
		} catch {
			consecutiveBlocks = 0;
			return undefined;
		}
		if (decision === undefined) {
			consecutiveBlocks = 0;
			return undefined;
		}
		consecutiveBlocks += 1;

		try {
			await pi.appendEntry(decision.entry.kind, decision.entry.data);
		} catch {
			// Telemetry is evidence, not permission to run an already-refused call.
		}
		return consecutiveBlocks >= CONSECUTIVE_BLOCK_LIMIT
			? { block: true, reason: decision.reason, terminate: true }
			: { block: true, reason: decision.reason };
	});

	pi.on("tool_result", async (event) => {
		try {
			const details = event.details;
			if (
				event.toolName === "edit" &&
				isRecord(details) &&
				details.satyrn === true &&
				details.ok === true &&
				isRecord(details.result)
			) {
				const { path, sha256 } = details.result;
				// Evidence we cannot read is not evidence of progress: an
				// absent or malformed digest leaves the window standing.
				if (typeof path === "string" && typeof sha256 === "string" && SHA256.test(sha256)) {
					breaker.noteChange(path, sha256);
				}
			}
		} catch {
			// A result we cannot read is not evidence that anything changed.
		}
		// This listener observes; the mutator owns the edit result itself.
		return undefined;
	});
}
