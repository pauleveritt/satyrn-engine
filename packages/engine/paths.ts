import { isAbsolute, posix, relative, resolve, sep } from "node:path";

/** Pi resolves tool paths against cwd, strips `@`, and accepts absolute paths
 * (core/tools/path-utils.js:42-44); this does the same and returns the repo-relative
 * POSIX path, or null when the path leaves the repo. Every guard keys by this. */
export function resolveWorkspacePath(repo: string, raw: string): string | null {
	const cleaned = raw.startsWith("@") ? raw.slice(1) : raw;
	const absolute = isAbsolute(cleaned) ? resolve(cleaned) : resolve(repo, cleaned);
	const rel = relative(resolve(repo), absolute);
	if (rel === "" || rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel)) return null;
	return rel.split(sep).join(posix.sep);
}
