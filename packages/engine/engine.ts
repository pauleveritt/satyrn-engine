import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/** Calls retained by the loop breaker. */
export const WINDOW = 20;

/** Matching admitted calls allowed before the next one is refused. */
export const THRESHOLD = 5;

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
}

function encodeJson(value: unknown, ancestors: WeakSet<object>): string | undefined {
	if (value === null) return "null";

	switch (typeof value) {
		case "boolean":
			return value ? "true" : "false";
		case "number":
			return Number.isFinite(value) ? JSON.stringify(value) : undefined;
		case "string":
			return JSON.stringify(value);
		case "object":
			break;
		default:
			return undefined;
	}

	if (ancestors.has(value)) return undefined;
	ancestors.add(value);
	try {
		if (Array.isArray(value)) {
			const encoded = value.map((item) => encodeJson(item, ancestors));
			return encoded.includes(undefined) ? undefined : `[${encoded.join(",")}]`;
		}

		const prototype = Object.getPrototypeOf(value);
		if (prototype !== Object.prototype && prototype !== null) return undefined;
		const encoded: string[] = [];
		for (const key of Object.keys(value).sort()) {
			const item = encodeJson((value as Record<string, unknown>)[key], ancestors);
			if (item === undefined) return undefined;
			encoded.push(`${JSON.stringify(key)}:${item}`);
		}
		return `{${encoded.join(",")}}`;
	} finally {
		ancestors.delete(value);
	}
}

function callKey(call: ToolCall): string | undefined {
	const input = encodeJson(call.input, new WeakSet());
	return input === undefined ? undefined : `${JSON.stringify(call.toolName)}:${input}`;
}

export function createLoopBreaker(): LoopBreaker {
	const admitted: string[] = [];
	const blockedByKey = new Map<string, number>();

	return {
		inspect(call: ToolCall): BlockDecision | undefined {
			let key: string | undefined;
			try {
				key = callKey(call);
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
			if (admitted.length > WINDOW) admitted.shift();
			return undefined;
		},
	};
}

export default function registerLoopBreaker(pi: ExtensionAPI): void {
	const breaker = createLoopBreaker();
	pi.on("tool_call", async (event) => {
		let decision: BlockDecision | undefined;
		try {
			decision = breaker.inspect({ toolName: event.toolName, input: event.input });
		} catch {
			return undefined;
		}
		if (decision === undefined) return undefined;

		try {
			await pi.appendEntry(decision.entry.kind, decision.entry.data);
		} catch {
			// Telemetry is evidence, not permission to run an already-refused call.
		}
		return { block: true, reason: decision.reason };
	});
}
