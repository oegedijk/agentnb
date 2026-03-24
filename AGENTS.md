# agentnb

## Purpose

`agentnb` is a project-scoped persistent Python REPL for coding agents. The important mental model is not "CLI over Jupyter"; it is "durable stateful execution with inspectable history and run records". Preserve that model when adding features.

## Architecture

- `agentnb.app`: application-service boundary. Keep CLI parsing and user-facing output shaping out of lower layers.
- `agentnb.runtime` + `agentnb.kernel`: kernel lifecycle and backend edge. Backend/protocol details should stop here.
- `agentnb.runs`: run-control boundary for foreground/background execution, follow/wait/cancel behavior, and durable run snapshots. Do not let `_background-run` subprocess details leak back upward.
- `agentnb.introspection`: helper execution and typed parsing for `vars`, `inspect`, and `reload`.
- `agentnb.journal` / `agentnb.history` / run storage: semantic history and durable run records are related but not interchangeable; keep provenance explicit.
- `agentnb.state`: owns `.agentnb/` layout and schema/version boundaries. New persisted resources should go through this boundary, not invent paths ad hoc.

## Design Bar

Prefer John Ousterhout style abstractions:

- Deep modules, shallow interfaces.
- Hide timing, polling, subprocess, protocol, and filesystem layout complexity behind clear boundaries.
- Avoid pass-through layers that only rename lower-level calls.
- Avoid special cases in public behavior when they can be represented as stable metadata or state.
- Keep machine contracts stable at the edges even when internal implementations change.

## Test Guidance

- Test behavior at the owning boundary. If logic lives in `agentnb.runs`, write direct `LocalRunManager` tests instead of reaching it indirectly through `ExecutionService`.
- Use `ExecutionService`/CLI tests for delegation, contract shape, and user-visible behavior, not for private manager internals.
- Prefer pytest fixtures, parametrization, and `pytest-mock` over repetitive setup.
- Assert meaningful outcomes: persisted run state, event ordering, provenance, error codes, session effects. Avoid tests that only restate implementation structure.
- Cover race-prone edges explicitly: background completion, cancellation, interrupt timing, stale worker reconciliation, and multi-step history/run interactions.
- Whenever you make a code change, run enough smoke tests with `uv run agentnb ...` commands to verify the changed code paths through the real CLI behavior, not just unit-level contracts.
- Use `docs/SMOKE_SCENARIOS.md` to choose or adapt relevant end-to-end workflows; do not run every scenario, only the smallest set needed to exercise the behavior you changed through the actual `agentnb` command surface.
