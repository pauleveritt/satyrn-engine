# Satyrn Engine

**Help Python developers write code their way, using Local AI.**

Satyrn is a two-repo effort to make that practical: a developer's own AI
partner works on their machine, in their repo, at their pace — and its
output arrives as something they review and own, never as a rewrite of
their working tree underneath them.

## What it owns — and doesn't

The engine owns:

- contract parsing and validation;
- contract-aware writable-path and revision enforcement for one replacement;
- candidate worktree, commit-or-discard, and receipt behavior;
- the Pi-side guards: loop breaker, writable-path scope, symbol preservation,
  command bounds — the last three only inside `/implement`;
- the Pi package and its thin TypeScript adapter;
- one real Pi attempt that connects isolation to bounded replacement;
- the internal Pi-adapter protocol and its compatibility fixtures.

It does **not** own workloads, grading, repeated runs, comparison
statistics, or contract authoring. Those live in the satyrn-evals
repository, or stay a main-agent skill. That split is deliberate: evals
runs the measurements, and the features built into the engine are the ones
that evidence surfaces — no machinery ahead of its contract.

> More: [glossary](docs/glossary.md) — the terms used here (`contract`,
> `adapter`, `protocol`, `refusal`, …), defined in this repository's own
> words.

Release one ships `/implement`: a developer-invoked bounded operation that
runs one piece of work in a fresh model context and its own worktree under a
derived contract, with the guards loaded, and returns a candidate plus a
compact receipt. Design and roadmap: the `satyrn-evals` repository,
`docs/superpowers/specs/2026-09-13-release-one-design.md`.

## License

Apache-2.0 — see [LICENSE](LICENSE).
