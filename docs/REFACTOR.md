# agentnb Refactor Guide

This document captures the current architectural refactor direction for
`agentnb`, using John Ousterhout's deep-module lens.

The core diagnosis is simple:

- the codebase has the right nouns
- several boundaries are still only named boundaries, not owning boundaries
- too much complexity is re-expressed in `app.py`, dict payload shaping, and
  projection/rendering code
- rich internal state gets flattened too early, so downstream modules become
  partial schema owners

The main architectural problem is **premature serialization**. Internal modules
should mostly exchange domain objects. Wire-oriented `TypedDict` payloads should
mostly exist at the response/serialization edge.

## Current Assessment

Strong deep modules:

- `kernel/backend.py` + `kernel/jupyter_protocol.py`
- `invocation.py`
- large parts of `runs/local_manager.py`
- much of `runtime.py`

Weak or shallow boundaries:

- `app.py`
- `execution.py`
- `ops.py`
- the `payloads.py` / `output.py` / `projection.py` / `compact.py` stack
- the `history.py` / `journal.py` / `recording.py` stack
- `state.py` as an accreting catch-all

## Main Leaks To Fix

### 1. App-Layer Policy Sink

`app.py` should orchestrate, not define command semantics. A thin application
layer should:

- resolve typed command context
- dispatch to a deep service
- translate typed outcomes and errors into the stable envelope

It should not own ad hoc command booleans, wait loops, or cross-cutting session
policy.

### 2. Split Ownership Of Session Access

The semantics of "can I safely use this session now?" must have one owner.
Ready, usable, idle, and active-run-blocked/helper access are one concern from
the app-facing point of view, even if they are implemented using runtime and
run-control internals.

### 3. Shallow Facades

`execution.py` and `ops.py` should either become real app-facing deep modules
or disappear as nominal forwarding layers.

### 4. Payloads As Internal Currency

`RunSnapshot`, `ExecPayload`, `StatusPayload`, and related payloads should not
be the main language between internal modules. Once modules exchange large dict
payloads, schema ownership leaks sideways into advice, rendering, projection,
and error handling.

### 5. Duplicated Execution Normalization

Execution normalization currently leaks across:

- `contracts.ExecutionResult`
- `runs.store.ExecutionRecord`
- `history._resolve_execution_metadata`
- compaction and projection layers

There should be one canonical internal execution transcript/outcome model that
owns:

- outputs
- events
- compatibility stdout/stderr/result views
- error details
- preview derivation

### 6. Provenance With Multiple Partial Owners

`recording.py`, `history.py`, and `journal.py` all understand command
provenance. That is workable today, but replay/verify will need a single honest
write-time provenance owner, not routine read-time reconstruction.

### 7. Split Presentation Policy

Command meaning is currently interpreted in several places:

- `app.py`
- `compact.py`
- `projection.py`
- `output.py`

Rendering text is separate from deciding which facts belong in a projected
view. Those should not remain parallel interpreters indefinitely.

### 8. `state.py` As Catch-All

`StateRepository` currently owns both session/runtime persistence and
persisted-resource domains such as snapshots and exports. That split should
become explicit before artifacts and persistence controls grow further.

### 9. CLI Leakage Into Run-Control

Background run bootstrapping currently depends on the CLI entrypoint, and some
command policy still lives in `cli.py`. That should be inverted so the CLI
calls run-control rather than being part of its implementation.

## Type / Contract Direction

The goal is not to replace every `TypedDict` with a dataclass one-for-one. The
goal is to introduce fewer, stronger canonical types at key seams and keep wire
payloads at the edge.

Important internal seams:

1. Command semantics/context
   - typed `CommandSemantics`
   - typed command/target resolution context
2. Session access/waiting
   - typed `SessionAccessRequest`
   - typed `SessionAccessOutcome`
   - one owner for ready/usable/idle/helper access semantics
3. Execution/run-control
   - canonical execution transcript/outcome model
   - typed submission/observation/cancellation/view models
4. Helpers/introspection
   - keep `KernelHelperResult[T]`
   - likely add a more explicit helper command/spec abstraction later
5. Errors
   - keep the external error envelope stable
   - reduce dependence on open-ended `AgentNBException.data`

## Refactor Order

1. Create typed command context and session access abstractions below `app.py`.
2. Establish canonical internal outcome models and push `TypedDict` use to serializers only.
3. Consolidate execution normalization around one transcript/output owner.
4. Consolidate provenance so replay/verify have one honest source.
5. Split session/runtime state from snapshot/export/artifact persistence.
6. Deepen or remove shallow facades, and remove the CLI dependency from background execution.

## Status

### Tranche 1: Typed Command Context + Session Access

Status: done

Completed in the current refactor:

- `app.py` now uses typed `CommandSemantics` instead of ad hoc boolean command
  knobs.
- `session_targeting.py` now owns typed command-context resolution via
  `ResolvedCommandContext`.
- starting-session preflight for read commands moved below `app.py`.
- `execution.py` now owns app-facing session access through:
  - `SessionAccessRequest`
  - `SessionAccessOutcome`
  - `wait_for_session_access(...)`
- app-facing waiting for:
  - `ready`
  - `usable`
  - `idle`
  - `helper`
  is now coordinated through the execution boundary instead of being split
  across app logic.
- `app.py` no longer owns the active-run-aware idle wait loop.
- `KernelIntrospection` now uses the execution-side session access seam.
- tests were rebalanced so behavior moved out of `app.py` is asserted more
  directly at the owning boundaries.

What this tranche intentionally did not do:

- it did not replace payload dicts with canonical internal outcome models
- it did not unify execution normalization yet
- it did not make provenance authoritative in one place yet
- it did not split `state.py`
- it did not remove the background-worker dependency on the CLI entrypoint

## Next Tranche

The next highest-value work is to establish canonical internal execution/outcome
models and reduce internal dependence on payload dicts. That should include:

- a single normalized execution transcript/outcome owner
- pushing `TypedDict` usage outward toward serializers and response shaping
- reducing duplicated compatibility/error/preview logic across execution,
  history, and run storage
