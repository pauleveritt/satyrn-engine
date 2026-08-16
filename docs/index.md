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

Phase E1 ships the `check` command: parse and validate a contract, lint
the repository path it names, and either accept it or refuse with a named
cause and a stable exit code. The roadmap and the current phase (E2, the
TypeScript adapter reaching E1) live in `ROADMAP.md` in this checkout.

```{toctree}
:maxdepth: 1
:caption: Usage

usage
glossary
```

```{toctree}
:maxdepth: 1
:caption: Development

contributing
sdd
superpowers/index
```
