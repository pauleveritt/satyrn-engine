import assert from "node:assert/strict";
import test from "node:test";

import scopeExtension, { admits, registerScope } from "../packages/engine/scope.ts";
import { resolveWorkspacePath } from "../packages/engine/paths.ts";
import { parseMutationContext } from "../packages/engine/mutator.ts";

const context = () => parseMutationContext(JSON.stringify({
	version: 1, repo: "/w", contract: "/w/c.yaml", revisions: {},
	writable_paths: ["src/*", "tests/*"], test_command: ["uv", "run", "python", "-m", "pytest", "-q"], symbols: {},
	carried: ["tests/test_keep.py", "tests/conftest.py", "pyproject.toml"], base_commit: "b".repeat(40),
}));

function fakePi() {
	const handlers = {}; const entries = [];
	return { pi: { on(event, h) { (handlers[event] ??= []).push(h); }, registerTool() {}, async appendEntry(kind, data) { entries.push({ kind, data }); } }, handlers, entries };
}

test("admits follows python fnmatch: star spans slashes, exact names match exactly", () => {
	assert.equal(admits(["src/*"], "src/pkg/deep/a.py"), true);
	assert.equal(admits(["tests/test_new.py"], "tests/test_new.py"), true);
	assert.equal(admits(["tests/test_new.py"], "tests/test_old.py"), false);
	assert.equal(admits(["tests/*"], "tests/deep/test_x.py"), true);
	assert.equal(admits(["src/?.py"], "src/a.py"), true);
	assert.equal(admits(["src/*"], "docs/a.md"), false);
});

test("a write inside the scope is admitted; outside, traversal and absolute-outside are refused with the writable list", async () => {
	const { pi, handlers, entries } = fakePi();
	registerScope(pi, context());
	const [handler] = handlers.tool_call;
	assert.equal(await handler({ toolCallId: "1", toolName: "write", input: { path: "src/a.py", content: "" } }), undefined);
	assert.equal(await handler({ toolCallId: "2", toolName: "write", input: { path: "/w/src/b.py", content: "" } }), undefined);
	for (const path of ["docs/a.md", "src/../../etc/x", "/etc/passwd"]) {
		const refusal = await handler({ toolCallId: path, toolName: "write", input: { path, content: "" } });
		assert.equal(refusal.block, true);
		assert.match(refusal.reason, /outside the contract's writable paths/);
		assert.match(refusal.reason, /Writable: src\/\*, tests\/\*/);
	}
	assert.equal(entries.length, 3);
	assert.deepEqual(entries[0], { kind: "scope_refused", data: { toolName: "write", toolCallId: "docs/a.md", path: "docs/a.md", carried: false } });
});

test("a carried path inside a writable pattern is refused as carried; a new test under a writable path is admitted", async () => {
	const { pi, handlers, entries } = fakePi();
	registerScope(pi, context());
	const [handler] = handlers.tool_call;
	for (const path of ["tests/test_keep.py", "@tests/conftest.py", "/w/pyproject.toml"]) {
		const refusal = await handler({ toolCallId: path, toolName: "edit", input: { path, edits: [] } });
		assert.equal(refusal.block, true);
		assert.match(refusal.reason, /carried from the accepted base and restored before every self-test/);
		// Final review fix: the old sentence ("Add new tests beside it
		// instead") pointed at a path scope usually refuses; it now names
		// the contract's actual writable paths instead.
		assert.match(refusal.reason, /Add new tests only under a writable path: src\/\*, tests\/\*\./);
	}
	assert.equal(await handler({ toolCallId: "n", toolName: "write", input: { path: "tests/test_new.py", content: "" } }), undefined);
	assert.deepEqual(entries.at(-1).data, { toolName: "edit", toolCallId: "/w/pyproject.toml", path: "pyproject.toml", carried: true });
});

test("edit paths are checked too; read and bash are not", async () => {
	const { pi, handlers } = fakePi();
	registerScope(pi, context());
	const [handler] = handlers.tool_call;
	assert.equal((await handler({ toolCallId: "1", toolName: "edit", input: { path: "docs/a.md", edits: [] } })).block, true);
	assert.equal(await handler({ toolCallId: "2", toolName: "read", input: { path: "docs/a.md" } }), undefined);
	assert.equal(await handler({ toolCallId: "3", toolName: "bash", input: { command: "cat docs/a.md" } }), undefined);
});

test("a write that drops a base-defined symbol is refused; a write that keeps every definition line is admitted", async () => {
	const { pi, handlers, entries } = fakePi();
	registerScope(pi, { ...context(), symbols: { "src/a.py": ["value", "Helper"] } });
	const [handler] = handlers.tool_call;
	const dropped = await handler({ toolCallId: "1", toolName: "write", input: { path: "src/a.py", content: "def value():\n    return 2\n" } });
	assert.equal(dropped.block, true);
	assert.match(dropped.reason, /this write would remove `Helper`, which the accepted base defines in src\/a\.py/);
	assert.deepEqual(entries.at(-1), { kind: "symbol_preserved", data: { toolName: "write", path: "src/a.py", symbols: ["Helper"] } });
	const kept = await handler({ toolCallId: "2", toolName: "write", input: { path: "src/a.py", content: "class Helper:\n    pass\n\n\ndef value():\n    return 2\n" } });
	assert.equal(kept, undefined);
	const fresh = await handler({ toolCallId: "3", toolName: "write", input: { path: "src/new.py", content: "x = 1\n" } });
	assert.equal(fresh, undefined);   // no base symbols for a new file
});

test("the default extension registers nothing without a mutation context", () => {
	const { pi, handlers } = fakePi();
	scopeExtension(pi, {});
	assert.deepEqual(handlers, {});
	const withContext = fakePi();
	scopeExtension(withContext.pi, { SATYRN_MUTATION_CONTEXT: JSON.stringify(context()) });
	assert.equal(withContext.handlers.tool_call.length, 1);
});
