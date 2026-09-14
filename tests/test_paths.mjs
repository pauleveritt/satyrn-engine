import assert from "node:assert/strict";
import test from "node:test";

import { resolveWorkspacePath } from "../packages/engine/paths.ts";

test("paths are resolved against the repo before matching", () => {
	assert.equal(resolveWorkspacePath("/w", "src/a.py"), "src/a.py");
	assert.equal(resolveWorkspacePath("/w", "@src/a.py"), "src/a.py");
	assert.equal(resolveWorkspacePath("/w", "/w/src/a.py"), "src/a.py");
	assert.equal(resolveWorkspacePath("/w", "./src/../src/a.py"), "src/a.py");
	assert.equal(resolveWorkspacePath("/w", "src/../../etc/x"), null);
	assert.equal(resolveWorkspacePath("/w", "/etc/x"), null);
	assert.equal(resolveWorkspacePath("/w", "@/w/src/a.py"), "src/a.py");
});
