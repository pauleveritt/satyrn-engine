import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import { MUTATION_CONTEXT_ENV, parseMutationContext, type MutationContext } from "./mutator.ts";
import { resolveWorkspacePath } from "./paths.ts";

const SCOPED_TOOLS = new Set(["write", "edit"]);

function patternToRegExp(pattern: string): RegExp {
	let source = "";
	for (const char of pattern) {
		if (char === "*") source += ".*";
		else if (char === "?") source += ".";
		else source += char.replace(/[.+^${}()|[\]\\]/g, "\\$&");
	}
	return new RegExp(`^${source}$`, "s");
}

/** Python fnmatch for the two wildcards the engine's contracts use; `*` spans `/`
 * (HP1's admission rule, satyrn-evals engine_contract.py:86-94 @ 00c3c18). */
export function admits(patterns: readonly string[], path: string): boolean {
	return patterns.some((pattern) => patternToRegExp(pattern).test(path));
}

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function registerScope(pi: ExtensionAPI, context: MutationContext): void {
	const carried = new Set(context.carried);
	const refuse = async (event: { toolName: string; toolCallId: string }, path: string, isCarried: boolean, reason: string) => {
		try {
			await pi.appendEntry("scope_refused", { toolName: event.toolName, toolCallId: event.toolCallId, path, carried: isCarried });
		} catch {
			// Telemetry is evidence, not permission.
		}
		return { block: true, reason };
	};
	pi.on("tool_call", async (event) => {
		if (!SCOPED_TOOLS.has(event.toolName) || !isRecord(event.input) || typeof event.input.path !== "string") return undefined;
		const resolved = resolveWorkspacePath(context.repo, event.input.path);
		if (resolved !== null && carried.has(resolved)) {
			return refuse(event, resolved, true,
				`${resolved} is carried from the accepted base and restored before every self-test; edits to it never count. ` +
				"Add new tests beside it instead.");
		}
		if (resolved !== null && admits(context.writable_paths, resolved)) return undefined;
		const shown = resolved ?? event.input.path;
		return refuse(event, shown, false,
			`Path outside the contract's writable paths: ${shown}. ` +
			`Writable: ${context.writable_paths.join(", ")}. ` +
			"Edit one of those, or stop and say which file the task needs.");
	});
}

export default function scopeExtension(pi: ExtensionAPI, environment: Readonly<Record<string, string | undefined>> = process.env): void {
	const contextText = environment[MUTATION_CONTEXT_ENV];
	if (contextText === undefined) return;
	let context: MutationContext;
	try {
		context = parseMutationContext(contextText);
	} catch {
		return;
	}
	registerScope(pi, context);
}
