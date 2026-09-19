import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import { MUTATION_CONTEXT_ENV, parseMutationContext } from "./mutator.ts";

/**
 * Guard 4: an unbounded command. Two `depth-3` ceiling cells spent 843 s and
 * 861 s inside one `find /` (scan_table.md, cells A1 and A4). Pi's bash tool
 * takes `timeout` in seconds with no default and kills the process group on
 * expiry (pi-bash-bounding.md §2); this guard only sets or clamps that field.
 * Frozen against scripts/suite_durations.json in satyrn-evals: 120 s is at
 * least twice the longest measured public suite; 300 s is twice the default.
 * Active only inside the /implement child (mutation context present).
 */
export const DEFAULT_TIMEOUT_SECONDS = 120;
export const MAX_TIMEOUT_SECONDS = 300;
const TIMED_OUT = "Command timed out after";

export type BoundAction = "set" | "clamped" | "kept";

export function boundTimeout(requested: unknown): { timeout: number; action: BoundAction } {
	if (typeof requested !== "number" || !Number.isFinite(requested) || requested <= 0) {
		return { timeout: DEFAULT_TIMEOUT_SECONDS, action: "set" };
	}
	if (requested > MAX_TIMEOUT_SECONDS) return { timeout: MAX_TIMEOUT_SECONDS, action: "clamped" };
	return { timeout: requested, action: "kept" };
}

export function bashSentence(seconds: number, testCommand: readonly string[]): string {
	const bound = `Commands here are bounded at ${seconds} seconds; on timeout Pi kills the process group.`;
	if (testCommand.length === 0) return bound;
	return `${bound} The self-test is "${testCommand.join(" ")}"; run it with the self_test tool.`;
}

/**
 * A route-proof cell (route-proof-engine-selfhost-run-record-gate) copied the
 * bound sentence straight into `src/satyrn_evals/cli.py` at turn 17: when a
 * model views a file through `cat`/`tail`/`sed -n`, an unfenced trailing
 * sentence reads as the file's own last line. `NOTE_MARKER` opens a delimited
 * block so an Engine note can never be mistaken for the command's output; the
 * em dash and brackets are chosen to be unlikely output from a real command.
 * `runner.ts`'s `DETECTED_SENTENCE` reuses this exact marker so the two notes
 * are recognizable as the same kind of thing wherever both land on one
 * result.
 */
export const NOTE_MARKER = "[satyrn-engine note — not part of the command's output]";

/** Fence one note behind a blank line and `NOTE_MARKER`, for appending after
 * a command's real output. */
export function fenceNote(sentence: string): string {
	return `\n\n${NOTE_MARKER}\n${sentence}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function registerBounds(pi: ExtensionAPI, testCommand: readonly string[]): void {
	const bounded = new Map<string, number>();
	// The note only needs to orient the model once per session (it never
	// changes) and on every timeout (the one case worth repeating, since it
	// explains an error the model just hit). Every other bash result is
	// returned untouched -- unfenced or not, a repeated reminder on every call
	// spends tokens for nothing after the first.
	let announced = false;
	const note = async (kind: string, data: Record<string, unknown>): Promise<void> => {
		try {
			await pi.appendEntry(kind, data);
		} catch {
			// Evidence, not permission.
		}
	};
	pi.on("tool_call", async (event) => {
		if (event.toolName !== "bash" || !isRecord(event.input)) return undefined;
		const { timeout, action } = boundTimeout(event.input.timeout);
		event.input.timeout = timeout;
		bounded.set(event.toolCallId, timeout);
		if (action !== "kept") await note("command_bounded", { toolCallId: event.toolCallId, action, timeout });
		return undefined;
	});
	pi.on("tool_result", async (event) => {
		if (event.toolName !== "bash") return undefined;
		const seconds = bounded.get(event.toolCallId) ?? DEFAULT_TIMEOUT_SECONDS;
		bounded.delete(event.toolCallId);
		const content = Array.isArray(event.content) ? [...event.content] : [];
		const last = content.length > 0 ? content[content.length - 1] : undefined;
		const lastText = isRecord(last) && last.type === "text" && typeof last.text === "string" ? last.text : undefined;
		// Final review fix: the phrase alone is not proof of a timeout --
		// output from `echo` or `grep` can put it in a *successful* result's
		// own text. Pi's bash tool only produces this sentence on a real
		// timeout, and marks that result `isError: true`; require both before
		// recording the firing.
		const timedOut = event.isError === true && lastText !== undefined && lastText.includes(TIMED_OUT);
		if (timedOut) await note("command_timed_out", { toolCallId: event.toolCallId, timeout: seconds });
		const isFirst = !announced;
		announced = true;
		if (!isFirst && !timedOut) return undefined;
		const fenced = fenceNote(bashSentence(seconds, testCommand));
		if (lastText !== undefined) {
			content[content.length - 1] = { ...last, text: `${lastText}${fenced}` };
		} else {
			content.push({ type: "text", text: `${NOTE_MARKER}\n${bashSentence(seconds, testCommand)}` });
		}
		return { content };
	});
}

export default function boundsExtension(pi: ExtensionAPI, environment: Readonly<Record<string, string | undefined>> = process.env): void {
	const contextText = environment[MUTATION_CONTEXT_ENV];
	if (contextText === undefined) return;
	try {
		registerBounds(pi, parseMutationContext(contextText).test_command);
	} catch {
		// No context, no guard: the developer's own session is not the /implement child.
	}
}
