# How we work

This repository runs on **spec-driven development**: every feature cycle
produces a design spec (what we're building and why) and an implementation
plan (the task-by-task decomposition), both committed before the code.

The cycle shape, from the superpowers workflow:

1. **Brainstorm** — clarify the idea into a design, present it, get
   approval.
2. **Spec** — write the validated design to
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`, commit it.
3. **Plan** — write the implementation plan to
   `docs/superpowers/plans/YYYY-MM-DD-<topic>.md`, commit it.
4. **Implement** — work the plan in reviewable cycles, each ending with a
   command a contributor can run and evidence that names a success fixture
   and a failure fixture.
5. **Record** — completed phases, and the withdrawn framings and retracted
   figures found along the way, move to the archive section of
   `ROADMAP.md` rather than being edited away.

## E3 verification record — 2026-08-19

The completed delivery phase was checked with its repository gates:

```console
uv run pytest
# 103 passed, 44 deselected

uv run pytest -m integration tests/test_integration_delivery.py -q
# 38 passed, 1 platform skip for the non-UTF-8 filename fixture

uv run pytest -m "" --cov
# 146 passed, 1 platform skip; 100% statements and 100% branches

uv run ruff check .
uv run pyrefly check
uv run --group docs sphinx-build -W -b html docs docs/_build/html
git diff --check
# all passed
```

Named evidence includes:

- candidate creation: `test_success_creates_candidate_with_exact_parent_and_paths`;
- discarded outcomes: `test_clean_root_reaches_no_changes_without_touching_source`,
  `test_failed_attempt_is_discarded_without_candidate`, and
  `test_timeout_kills_same_process_group_descendant`;
- refusal and cleanup precedence:
  `test_dirty_source_is_refused_before_worktree` and
  `test_locked_worktree_reports_retained_path_for_manual_recovery`;
- atomic publication:
  `test_two_delivery_processes_publish_exactly_one_candidate`;
- lifecycle edges:
  `test_registration_interrupt_after_real_add_leaves_no_stale_worktree`,
  `test_attached_head_at_base_is_discarded_without_candidate`, and
  `test_caller_head_can_advance_after_preflight_without_moving_candidate_base`;
- environment and receipt transport:
  `test_engine_git_ignores_caller_date_overrides_but_command_keeps_them` and
  `test_closed_receipt_pipe_exits_one_without_hiding_published_candidate`.

The timeout fixture starts a same-process-group descendant with a delayed
write. The absent sentinel after delivery returns is the evidence that teardown
finishes before Git cleanup. The registration-interrupt fixture compares the
complete pre/post `git worktree list --porcelain -z` state, so it also rejects a
stale prunable registration.

The disciplines review holds you to:

- **Concept budget** — new jargon is a cost against a 5–10 h/wk
  contributor's ability to hold the design in mind.
- **Non-vacuity** — a refusal test has a sibling success test.
- **Verify, don't assert** — demonstrate a claim, don't state it.
