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
cause and a stable exit code. Phase E2 ships the Pi adapter:
`/implement CONTRACT` reaches the same verdict through the TypeScript
adapter, which starts the engine as a subprocess and speaks one-shot,
versioned JSON — see {doc}`usage` and {doc}`architecture`. The roadmap
and the current phase live in `ROADMAP.md` in this checkout.

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
