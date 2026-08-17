# Satyrn Engine

**Turns a bounded contract into a candidate change without modifying the
caller's working tree.**

The engine is the Python core of the two-repo satyrn effort: a library and
CLI that parse and validate a bounded contract, enforce writable paths and
revision state, and deliver a candidate change as a reviewable ref or a
receipt — never writing to the caller's tree. Pi integration is a thin
TypeScript adapter over a one-shot, versioned JSON protocol.

What it deliberately does **not** own: workloads, grading, repeated runs,
comparison statistics, or contract authoring. Those live in the
satyrn-evals repository, or stay a main-agent skill.

## Status

- _E1_ — it installs and refuses. `satyrn-engine check --repo REPO CONTRACT`
  parses, validates, and path-lints a contract, refusing with a named cause
  and a stable exit code. ({doc}`spec <superpowers/specs/2026-08-16-e1-check-design>`, {doc}`plan <superpowers/plans/2026-08-16-e1-check>`)
- _E2_ — the adapter reaches E1. `/implement CONTRACT` reaches the same
  refusal through the TypeScript adapter over one-shot, versioned JSON —
  one Python process per operation. ({doc}`spec <superpowers/specs/2026-08-16-e2-adapter-reaches-e1-design>`, {doc}`plan <superpowers/plans/2026-08-16-e2-adapter-reaches-e1>`)

See {doc}`usage` for both surfaces and {doc}`architecture` for the shape;
the roadmap and the current phase live in `ROADMAP.md` in this checkout.

```{toctree}
:maxdepth: 1
:caption: Usage

usage
glossary
```

```{toctree}
:maxdepth: 1
:caption: Development

architecture
contributing
sdd
superpowers/index
```
