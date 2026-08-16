import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * The engine — the package you install: one self-contained extension that
 * ships both guards.
 *
 * Install: `cp .pi/extensions/engine.ts ~/.pi/agent/extensions/`.
 *
 * The guards are passive steering: they watch tool calls and refuse two
 * failure modes, nothing else. This file imports nothing local. The two
 * guards below are copied verbatim from `extensions/guards/loop-breaker.ts`
 * and `extensions/guards/preserve-symbols.ts`, with their shared types
 * inlined from `extensions/guards/types.ts`. The pinning tests in
 * `extensions/guards/guards.test.ts` ("the engine bundle artifact") keep the
 * copies and the sources from drifting apart.
 */

/** One tool call, as both Pi and a recorded transcript can describe it. */
export interface ToolCall {
	toolName: string;
	/** Arguments, whatever shape the tool takes. */
	input: unknown;
	/**
	 * The path this call acts on, when it has one. Supplied by the caller
	 * rather than derived here, because the derivation differs between a
	 * live Pi event and a recorded transcript, and a guard should not care.
	 */
	target?: string | null;
}

/** What a guard decides. `undefined` means "no opinion, let it through". */
export interface Block {
	block: true;
	/** Shown to the model as an error tool result. Steers; does not merely refuse. */
	reason: string;
	/** Appended to telemetry as `entry_appended`, so a block is observable. */
	entry: { kind: string; data: Record<string, unknown> };
}

export type Decision = Block | undefined;

export interface Guard {
	readonly name: string;
	/** Called once per tool call, in order. Guards are stateful across a run. */
	inspect(call: ToolCall): Decision;
}

// ---------------------------------------------------------------------------
// Guard #1 — the loop breaker.
// ---------------------------------------------------------------------------

/** Calls remembered. Older ones fall out and stop counting. */
export const WINDOW = 20;

/** Identical calls within the window before the next one is refused. */
export const THRESHOLD = 5;

/**
 * Stable key for a call: same tool, same arguments, regardless of key order.
 * `JSON.stringify` alone is order-sensitive, so two identical calls whose
 * arguments serialised differently would not be recognised as repeats.
 */
export function callKey(toolName: string, input: unknown): string {
	const stable = (value: unknown): unknown => {
		if (Array.isArray(value)) return value.map(stable);
		if (value && typeof value === "object") {
			return Object.fromEntries(
				Object.entries(value as Record<string, unknown>)
					.sort(([a], [b]) => a.localeCompare(b))
					.map(([k, v]) => [k, stable(v)]),
			);
		}
		return value;
	};
	return `${toolName}\u0000${JSON.stringify(stable(input))}`;
}

export function createLoopBreaker(
	window: number = WINDOW,
	threshold: number = THRESHOLD,
): Guard {
	// Admitted calls only. A blocked call does not enter the window, so a
	// model that keeps retrying stays blocked rather than sliding the
	// repeats out of view and being let through again.
	const recent: string[] = [];
	const blocked = new Map<string, number>();

	return {
		name: "loop-breaker",
		inspect(call: ToolCall): Decision {
			const key = callKey(call.toolName, call.input);
			const seen = recent.filter((entry) => entry === key).length;

			if (seen >= threshold) {
				const times = (blocked.get(key) ?? 0) + 1;
				blocked.set(key, times);
				return {
					block: true,
					// The reason steers rather than merely refusing. A bare
					// "no" invites a sixth attempt; naming the repetition and
					// stating that the answer will not change gives the model
					// somewhere to go.
					reason:
						`You have already run this exact ${call.toolName} call ${seen} times ` +
						`in a row and the result will not change. Do not repeat it. ` +
						`Use what you already know and take the next concrete action — ` +
						`if you were looking for files and found none, create them.`,
					entry: {
						kind: "loop_broken",
						data: { tool: call.toolName, repeats: seen, blockedSoFar: times },
					},
				};
			}

			recent.push(key);
			if (recent.length > window) recent.shift();
			return undefined;
		},
	};
}

// ---------------------------------------------------------------------------
// Guard #2 — preserve symbols.
// ---------------------------------------------------------------------------

/** Tools whose payload this guard understands. */
const EDIT_TOOLS = new Set(["edit"]);

/**
 * What counts as a public symbol. Deliberately shallow: these are text
 * patterns, not a parse. A parser would be more precise and would also make
 * the guard language-specific and far larger than the loop-breaker's shape.
 */
const SYMBOL_PATTERNS: { kind: string; pattern: RegExp }[] = [
	{ kind: "function", pattern: /^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)/gm },
	{ kind: "class", pattern: /^\s*class\s+([A-Za-z_]\w*)/gm },
	// A route decorator names a URL rather than an identifier, and losing one
	// is exactly the observed failure. Matches @app.get("/x"), @router.post('/y').
	{ kind: "route", pattern: /^\s*@\w+\.(?:get|post|put|patch|delete)\(\s*["']([^"']+)["']/gm },
];

export interface FoundSymbol {
	kind: string;
	name: string;
}

export function symbolsIn(text: string): FoundSymbol[] {
	const found: FoundSymbol[] = [];
	for (const { kind, pattern } of SYMBOL_PATTERNS) {
		// `matchAll` needs a fresh lastIndex; the patterns are module-level
		// and global, so reusing them across calls without this leaks state.
		pattern.lastIndex = 0;
		for (const match of text.matchAll(pattern)) {
			found.push({ kind, name: match[1] });
		}
	}
	return found;
}

interface EditPayload {
	path?: unknown;
	edits?: unknown;
}

/**
 * Symbols present in the `oldText`s of a call but absent from *every*
 * `newText` of the same call.
 *
 * The union across `newText`s is load-bearing. Pi 0.83.0's edit input is
 * `{path, edits: [{oldText, newText}]}` — a **multi-edit array**, not the
 * `oldString`/`newString` pair an earlier draft of this guard assumed and
 * which would have matched nothing. A model that moves a function by
 * deleting it in one entry and re-adding it in another is refactoring, not
 * destroying, and comparing entry-by-entry would refuse it.
 */
export function deletedSymbols(input: unknown): FoundSymbol[] {
	const payload = (input ?? {}) as EditPayload;
	if (!Array.isArray(payload.edits)) return [];

	const olds: string[] = [];
	const news: string[] = [];
	for (const entry of payload.edits) {
		const { oldText, newText } = (entry ?? {}) as Record<string, unknown>;
		if (typeof oldText === "string") olds.push(oldText);
		if (typeof newText === "string") news.push(newText);
	}

	const surviving = new Set(
		news.flatMap((text) => symbolsIn(text)).map((s) => `${s.kind}:${s.name}`),
	);

	const deleted: FoundSymbol[] = [];
	const seen = new Set<string>();
	for (const symbol of olds.flatMap(symbolsIn)) {
		const key = `${symbol.kind}:${symbol.name}`;
		if (surviving.has(key) || seen.has(key)) continue;
		seen.add(key);
		deleted.push(symbol);
	}
	return deleted;
}

function describe(symbol: FoundSymbol): string {
	return symbol.kind === "route" ? `the route \`${symbol.name}\`` : `\`${symbol.name}\``;
}

export function createPreserveSymbols(): Guard {
	let refused = 0;

	return {
		name: "preserve-symbols",
		inspect(call: ToolCall): Decision {
			if (!EDIT_TOOLS.has(call.toolName)) return undefined;

			const deleted = deletedSymbols(call.input);
			if (deleted.length === 0) return undefined;

			refused += 1;
			const named = deleted.map(describe).join(", ");
			return {
				block: true,
				reason:
					`This edit removes ${named}, which exists in the file already and ` +
					`is not replaced by anything in the new text. ` +
					`If you meant to ADD something, do not use existing code as the ` +
					`anchor — anchor on a line you are keeping and include it unchanged ` +
					`in the new text, so the new code is inserted alongside rather than ` +
					`on top of it. ` +
					`If you genuinely meant to delete ${named}, say so and do it in a ` +
					`separate edit.`,
				entry: {
					kind: "symbol_preserved",
					data: {
						path: typeof (call.input as EditPayload)?.path === "string"
							? (call.input as EditPayload).path
							: null,
						deleted: deleted.map((s) => `${s.kind}:${s.name}`),
						refusedSoFar: refused,
					},
				},
			};
		},
	};
}

// ---------------------------------------------------------------------------
// Adapter — one default factory that registers both guards on `tool_call`.
// ---------------------------------------------------------------------------

// The guard instances live at module scope, so their state (the loop
// breaker's recent/blocked history, the preserve-symbols refusal count) is
// shared across registrations of the default export. One `default()` call
// per session makes that equivalent to per-registration state, but it is a
// difference from the standalone loop-breaker extension, whose `recent` and
// `blocked` state is created fresh inside the factory each registration.
const loopBreaker = createLoopBreaker();
const preserveSymbols = createPreserveSymbols();
const GUARDS: Guard[] = [loopBreaker, preserveSymbols];

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event) => {
		const call: ToolCall = {
			toolName: event.toolName,
			input: event.input,
			target:
				event.input && typeof event.input === "object" && "path" in event.input
					? (event.input as { path?: string }).path ?? null
					: null,
		};
		for (const guard of GUARDS) {
			const decision = guard.inspect(call);
			if (decision?.block) {
				pi.appendEntry(decision.entry.kind, decision.entry.data);
				return { block: true, reason: decision.reason };
			}
		}
	});
}
