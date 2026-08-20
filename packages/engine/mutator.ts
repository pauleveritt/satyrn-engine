import { spawn } from "node:child_process";
import { isAbsolute } from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

import {
	AdapterRefusal,
	DEFAULT_DEADLINE_MS,
	PROTOCOL_VERSION,
	exchange,
	type EngineResponse,
	type Spawner,
} from "./orchestrator.ts";

export const MUTATION_CONTEXT_ENV = "SATYRN_MUTATION_CONTEXT";

const SHA256 = /^[0-9a-f]{64}$/;

export interface MutationContext {
	readonly version: 1;
	readonly repo: string;
	readonly contract: string;
	readonly revisions: Readonly<Record<string, string>>;
}

export interface EditReplacement {
	readonly oldText: string;
	readonly newText: string;
}

export interface EditInput {
	readonly path: string;
	readonly edits: readonly [EditReplacement];
}

export interface ReplacementResult {
	readonly path: string;
	readonly sha256: string;
}

export interface ReplacementResponse extends EngineResponse {
	readonly result: ReplacementResult | null;
}

export interface MutationToolDetails {
	readonly satyrn: true;
	readonly ok: boolean;
	readonly code: string;
	readonly result: ReplacementResult | null;
}

export interface MutationToolResult {
	readonly content: readonly [{ readonly type: "text"; readonly text: string }];
	readonly details: MutationToolDetails;
}

export type ExchangeRequest = (request: string) => Promise<EngineResponse>;

export type MutationEnvironment = Readonly<Record<string, string | undefined>>;

export interface Mutator {
	execute(toolCallId: string, input: unknown): Promise<MutationToolResult>;
}

export function createEngineExchange(
	spawner: Spawner,
	engineRepo: string,
	deadlineMs: number = DEFAULT_DEADLINE_MS,
): ExchangeRequest {
	return (request) => exchange(spawner, request, engineRepo, deadlineMs);
}

const EditParameters = {
	type: "object",
	additionalProperties: false,
	required: ["path", "edits"],
	properties: {
		path: { type: "string", minLength: 1 },
		edits: {
			type: "array",
			minItems: 1,
			maxItems: 1,
			items: {
				type: "object",
				additionalProperties: false,
				required: ["oldText", "newText"],
				properties: {
					oldText: { type: "string", minLength: 1 },
					newText: { type: "string" },
				},
			},
		},
	},
} as const;

function isRecord(value: unknown): value is Record<string, unknown> {
	return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function parseMutationContext(text: string): MutationContext {
	let parsed: unknown;
	try {
		parsed = JSON.parse(text);
	} catch {
		throw new AdapterRefusal("MUTATION_CONTEXT_INVALID", "mutation context is not valid JSON");
	}
	if (
		!isRecord(parsed) ||
		parsed.version !== PROTOCOL_VERSION ||
		typeof parsed.repo !== "string" ||
		!isAbsolute(parsed.repo) ||
		typeof parsed.contract !== "string" ||
		!isAbsolute(parsed.contract) ||
		!isRecord(parsed.revisions)
	) {
		throw new AdapterRefusal("MUTATION_CONTEXT_INVALID", "mutation context has an unexpected shape");
	}
	for (const [path, revision] of Object.entries(parsed.revisions)) {
		if (!path || typeof revision !== "string" || !SHA256.test(revision)) {
			throw new AdapterRefusal("MUTATION_CONTEXT_INVALID", "mutation context has an invalid revision map");
		}
	}
	return {
		version: PROTOCOL_VERSION,
		repo: parsed.repo,
		contract: parsed.contract,
		revisions: parsed.revisions as Record<string, string>,
	};
}

function parseEditInput(input: unknown): EditInput {
	if (!isRecord(input) || typeof input.path !== "string" || input.path.length === 0) {
		throw new AdapterRefusal("INVALID_REQUEST", "edit path must be a non-empty string");
	}
	if (!Array.isArray(input.edits) || input.edits.length !== 1) {
		throw new AdapterRefusal("INVALID_REQUEST", "E4 edit requires exactly one replacement");
	}
	const [replacement] = input.edits;
	if (
		!isRecord(replacement) ||
		typeof replacement.oldText !== "string" ||
		replacement.oldText.length === 0 ||
		typeof replacement.newText !== "string"
	) {
		throw new AdapterRefusal("INVALID_REQUEST", "edit replacement requires non-empty oldText and string newText");
	}
	return {
		path: input.path,
		edits: [{ oldText: replacement.oldText, newText: replacement.newText }],
	};
}

export function buildReplacementRequest(
	context: MutationContext,
	input: EditInput,
	expectedSha256: string,
): string {
	const [replacement] = input.edits;
	return JSON.stringify({
		version: PROTOCOL_VERSION,
		operation: "replace",
		repo: context.repo,
		contract: context.contract,
		path: input.path,
		expected_sha256: expectedSha256,
		old_text: replacement.oldText,
		new_text: replacement.newText,
	});
}

export function parseReplacementResponse(response: EngineResponse): ReplacementResponse {
	if (response.ok) {
		if (
			response.code !== "OK" ||
			!isRecord(response.result) ||
			typeof response.result.path !== "string" ||
			typeof response.result.sha256 !== "string" ||
			!SHA256.test(response.result.sha256)
		) {
			throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "successful replacement response has an unexpected shape");
		}
		return response as ReplacementResponse;
	}
	if (response.result !== null) {
		throw new AdapterRefusal("ENGINE_MALFORMED_RESPONSE", "refused replacement response must have a null result");
	}
	return response as ReplacementResponse;
}

function successResult(replacement: ReplacementResult): MutationToolResult {
	return {
		content: [{ type: "text", text: `Replaced ${replacement.path}; sha256=${replacement.sha256}` }],
		details: { satyrn: true, ok: true, code: "OK", result: replacement },
	};
}

function refusalResult(code: string, message: string): MutationToolResult {
	return {
		content: [{ type: "text", text: `${code}: ${message}` }],
		details: { satyrn: true, ok: false, code, result: null },
	};
}

export function createMutator(context: MutationContext, exchangeRequest: ExchangeRequest): Mutator {
	const revisions = new Map(Object.entries(context.revisions));
	return {
		async execute(_toolCallId: string, rawInput: unknown): Promise<MutationToolResult> {
			try {
				const input = parseEditInput(rawInput);
				const expectedSha256 = revisions.get(input.path);
				if (expectedSha256 === undefined) {
					return refusalResult("REVISION_UNAVAILABLE", `no captured revision is available for ${input.path}`);
				}
				const request = buildReplacementRequest(context, input, expectedSha256);
				const response = parseReplacementResponse(await exchangeRequest(request));
				if (!response.ok || response.result === null) {
					return refusalResult(response.code, response.message);
				}
				if (response.result.path !== input.path) {
					return refusalResult("ENGINE_MALFORMED_RESPONSE", "engine returned a different replacement path");
				}
				revisions.set(response.result.path, response.result.sha256);
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

export function registerMutator(pi: ExtensionAPI, context: MutationContext, exchangeRequest: ExchangeRequest): void {
	const mutator = createMutator(context, exchangeRequest);
	pi.registerTool({
		name: "edit",
		label: "Bounded revision-checked edit",
		description: "Replace one exact unique text anchor in one contract-declared file.",
		parameters: EditParameters,
		execute: mutator.execute,
	});
	pi.on("tool_result", async (event) => {
		if (event.toolName !== "edit" || !isRecord(event.details) || event.details.satyrn !== true) {
			return undefined;
		}
		return event.details.ok === true ? undefined : { isError: true };
	});
}

export default function mutationExtension(
	pi: ExtensionAPI,
	environment: MutationEnvironment = process.env,
	exchangeRequest?: ExchangeRequest,
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
	registerMutator(
		pi,
		context,
		exchangeRequest ?? createEngineExchange(spawn, engineRepo),
	);
}
