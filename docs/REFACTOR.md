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

## Refactor Rule: Clean Cutovers

Each tranche should end in a clean cut at the seam it changes.

- when a boundary moves, internal callers should move fully to the new owning
  interface
- do not preserve dual internal interfaces just to soften the transition
- do not keep wrapper/shim layers around once they stop adding real semantic
  value
- compatibility belongs only at deliberate external or wire-level edges, not
  between internal modules

This refactor is explicitly not trying to preserve every pre-refactor internal
API. The goal is to leave behind clearer owning boundaries, not a pile of
temporary adapters that become permanent.

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

### Tranche 2: Canonical Execution Transcript / Outcome

Status: done

Completed in the current refactor:

- `execution_models.py` now owns canonical internal execution normalization
  through `ExecutionTranscript` and `ExecutionOutcome`.
- `contracts.ExecutionResult` now normalizes through the canonical outcome
  model instead of duplicating compatibility projection logic.
- `runs.store.ExecutionRecord` now keeps a transcript and derives compatibility
  fields from the normalized outcome model.
- history/journal/recording paths now accept `ExecutionOutcome` so preview,
  error, and failure-origin logic does not have to be recomputed from ad hoc
  field sets.
- exec/run compaction now prefers `ExecutionRecord` and canonical outcome data
  instead of partially shaped payload dicts.
- cancelled-run projection was tightened so persisted terminal state remains
  authoritative over raw transcript error events when rehydrating an
  `ExecutionRecord` outcome.
- app-facing command handlers for `start`, `status`, `wait`, `exec`, `reset`,
  `runs-*`, `vars`, `inspect`, `reload`, and `history` now return typed
  internal command data instead of assembling payload dicts directly in
  `app.py`.
- `command_data.py` now owns the typed app-facing command-data layer for
  session state, execution/run views, helper views, and history views.
- `response_serialization.py` now owns the serializer boundary for:
  - full JSON data shaping
  - agent-profile projection
  - exec/run/result/error/preview compatibility shaping
- `contracts.CommandResponse` can now carry typed internal command data while
  preserving the stable external response envelope through serialization at the
  edge.
- `projection.py` and `output.py` now consume typed command data for the
  covered command families instead of reinterpreting ad hoc payload dicts.
- app/output-facing run lookup and run list paths no longer depend on mixed
  `ExecutionRecord | Mapping` seams; they normalize into dedicated typed run
  view data before serialization.
- the remaining passive/session command families now also return first-class
  typed command data instead of payload dicts:
  - `doctor`
  - session-management commands
  - `interrupt`
  - `stop`
  - `runs-cancel`
- advice generation now consumes typed command data on the normal success path
  instead of pre-serialized response mappings.
- `compact.py` has been reduced to low-level preview/ANSI/traceback helpers;
  command-level compaction and compatibility shaping now live in
  `response_serialization.py`.
- runtime/run-control internals now return typed outcomes for doctor, session
  listing/deletion, and run cancellation instead of app-facing payload dicts.

What is still incomplete:

- compatibility adapters still exist for non-app callers and legacy tests:
  - `SerializedCommandData`
  - `compat_command_data(...)`
  - projection/output fallbacks for responses created from plain mappings
- some public/wire payload `TypedDict`s still remain at explicit compatibility
  edges such as package API helpers and serializer output contracts.
- app-facing errors still rely on mapping payloads via `AgentNBException.data`
  rather than a canonical typed internal error model.

### Tranche 3: Typed Error Context + Edge-Only Compatibility

Status: done

Completed in the current refactor:

- `errors.py` now owns canonical typed internal error context through
  `ErrorContext`.
- `AgentNBException` and the concrete exception types now carry typed error
  context instead of using open-ended `data` mappings as their internal
  transport.
- `AgentNBException.data` remains as a derived compatibility view so the
  external error envelope and persisted `error_data` shape remain stable.
- app-facing error handling in `app.py` now consumes typed error context for:
  - response session-id/session-source resolution
  - advice generation
  - stable envelope shaping
- `AdviceContext` now carries typed error context, and error-path advice no
  longer depends on raw mapping payloads for the covered cases.
- helper/introspection error augmentation now composes helper access facts into
  typed error context rather than mutating arbitrary payload dicts.
- app-facing `exec`/`reset` failure responses no longer serialize payloads
  early in `app.py`; typed command data now flows to the response edge and is
  serialized there.
- execution normalization and persisted run snapshots now derive `error_data`
  from typed error context rather than from ad hoc `AgentNBException.data`
  dicts.
- selector/session-targeting/CLI invalid-input and ambiguity errors now
  construct typed context directly instead of assembling loose error payload
  mappings.
- typed error-context merge now preserves explicit null-clearing semantics for
  nullable compatibility fields such as `active_execution_id`.
- helper-access merge semantics were aligned so typed error-context merging and
  introspection-side helper-access merging preserve the same
  `initial_runtime_state` for the same observed helper history.

What this tranche intentionally did not do:

- it did not make provenance authoritative in one place
- it did not remove all compatibility adapters
- it did not eliminate every mapping-based error payload at explicit
  compatibility boundaries
- it did not yet move run-control wrapper failures to typed run command data in
  all layers; `runs/local_manager.py` still rethrows some failures with a
  compatibility mapping payload plus typed error context to avoid a boundary
  cycle

What is still incomplete:

- `SerializedCommandData` / `compat_command_data(...)` still exist for explicit
  compatibility callers and output/projection fallbacks.
- some package API and serializer contracts still legitimately expose payload
  `TypedDict`s at the public wire edge.
- `output.py` and `projection.py` still keep some mapping-based fallbacks for
  responses that were not built from typed command data.

### Tranche 4: Authoritative Provenance + Explicit State Cutover

Status: done

Completed in the current refactor:

- `recording.py` now owns first-class write-time provenance through explicit
  command record specs instead of relying on read-time classification.
- `history.py` now persists canonical provenance metadata on every history
  record:
  - `classification`
  - `provenance_detail`
- exec/reset/vars/inspect/reload now author provenance at record creation time
  with stable semantics for:
  - replayable vs inspection vs control vs internal
  - user command vs kernel execution
- `journal.py` is now a selector/query layer over persisted history records and
  persisted run journal entries; it no longer reconstructs execution
  provenance from partial run state.
- run persistence now treats terminal exec/reset records without persisted
  `journal_entries` as invalid state instead of silently rebuilding synthetic
  history on read.
- `runs/executor.py` progress snapshots were tightened so streamed error events
  do not prematurely flip an active background run into an apparent terminal
  error before finalization.
- the state boundary now owns the cutover explicitly:
  - state schema advanced to `2`
  - `history` and `executions` resource versions advanced to `2`
  - `StateRepository.ensure_compatible()` now rejects stale resource versions
  - existing versioned `.agentnb/` state without a manifest now fails fast at
    the state boundary instead of later in execution-history readers
- direct recorder tests now assert provenance construction at the owning module.
- runtime/CLI compatibility tests now cover the upgrade boundary rather than
  relying only on low-level execution-store tests.

What this tranche intentionally did not do:

- it did not split `state.py` into separate persistence-domain modules
- it did not remove the remaining command-data compatibility adapters
- it did not deepen or remove the `execution.py` / `ops.py` facades
- it did not remove the CLI dependency from background execution

What is still incomplete:

- `state.py` still mixes:
  - session/runtime persistence
  - execution/history persistence
  - snapshot/export/artifact persistence
  - manifest/resource-version policy
- `execution.py` and `ops.py` still act partly as app-facing facades and partly
  as delegation layers rather than clearly deep owning modules.
- background run bootstrapping still depends on the CLI entrypoint through the
  hidden `_background-run` command.
- `SerializedCommandData` / `compat_command_data(...)` and some
  mapping-oriented output/projection fallbacks still remain at explicit
  compatibility edges.

### Tranche 5: Split State Into Explicit Persistence Domains

Status: done

Completed in the current refactor:

- the state boundary now has explicit owning modules instead of one monolithic
  repository:
  - `state_layout.py`
  - `state_manifest.py`
  - `state_runtime.py`
  - `state_persisted_resources.py`
- `StateLayout` now owns `.agentnb/` path layout, registered state resources,
  and session-runtime file paths.
- `StateManifestRepository` now owns manifest I/O and compatibility validation.
- `RuntimeStateRepository` now owns:
  - session preferences
  - remembered current-session state
  - runtime artifact cleanup
  - session-runtime file access
- `PersistedResourceRepository` now owns snapshot/export descriptor lifecycle
  and persisted resource selection/validation.
- `StateRepository` remains as an internal compatibility facade, but its role is
  now composition and delegation rather than broad policy ownership.
- `HistoryStore` and `ExecutionStore` now own their resource-version
  requirements directly and reject stale manifests on both read and write
  paths.
- simple `.agentnb/` path consumers now use `StateLayout` directly instead of
  reaching through the compatibility facade.
- state-boundary tests were split to the owning modules instead of keeping one
  monolithic `test_state.py`.

What this tranche intentionally did not do:

- it did not remove `StateRepository`
- it did not deepen or remove the `execution.py` / `ops.py` facades
- it did not remove the CLI dependency from background execution
- it did not remove the remaining command-data compatibility adapters

What is still incomplete:

- `execution.py` still owns a mixed surface:
  - app-facing session access policy
  - run submission/query/cancel delegation
  - active-run probing glue
- `ops.py` is still largely a wrapper around `KernelIntrospection` rather than
  a clearly deep semantic boundary.
- background run bootstrapping still depends on the hidden CLI
  `_background-run` command rather than a run-control-owned worker entrypoint.
- `SerializedCommandData` / `compat_command_data(...)` and some
  mapping-oriented output/projection fallbacks still remain at explicit
  compatibility edges.

### Tranche 6: Deepen Execution Boundary And Remove `ops.py`

Status: done

Completed in the current refactor:

- `execution.py` is now the real app-facing execution boundary rather than a
  thin wrapper layer.
- `ExecutionService` now owns typed internal execution/run request models:
  - `ExecutionCommandRequest`
  - `RunListRequest`
  - `RunRetrievalRequest`
  - `RunRetrievalOutcome`
  - `RunCancelRequest`
- app-facing exec/reset/run-lookup paths now use those typed execution-side
  requests instead of scattering wrapper semantics across `app.py`.
- the execution boundary now has one internal run API instead of parallel
  wrapper and request-object paths:
  - `list_runs(request=...)`
  - `retrieve_run(...)`
  - `cancel_run(request=...)`
- run lookup semantics now live in `RunRetrievalRequest.mode` rather than in a
  second layer of wrapper methods like `get_run(...)` / `wait_for_run(...)` /
  `follow_run(...)`.
- active-run lookup for idle/helper coordination is now owned by run-control
  through `RunManager.active_run_for_session(...)` instead of being rebuilt in
  `execution.py` by scanning generic run listings.
- `ops.py` and `NotebookOps` were removed entirely.
- `KernelIntrospection` is now the sole semantic boundary for:
  - `vars`
  - `inspect`
  - `reload`
- `KernelIntrospection` now depends only on a narrow session-access seam rather
  than the whole execution service surface.
- CLI and app wiring now construct and use `KernelIntrospection` directly
  instead of routing helper behavior through `NotebookOps`.
- tests were rebalanced accordingly:
  - the `NotebookOps` unit seam was removed
  - app/CLI tests now target `KernelIntrospection` or typed execution requests
  - execution tests now target the typed execution boundary directly

What this tranche intentionally did not do:

- it did not remove the CLI dependency from background execution
- it did not remove the remaining command-data compatibility adapters
- it did not eliminate all mapping-based output/projection fallbacks

What is still incomplete:

- background run bootstrapping still depends on the hidden CLI
  `_background-run` command rather than a run-control-owned worker entrypoint.
- `SerializedCommandData` / `compat_command_data(...)` and some
  mapping-oriented output/projection fallbacks still remain at explicit
  compatibility edges.

### Tranche 7: Run-Control-Owned Background Worker Cutover

Status: done

Completed in the current refactor:

- `runs/worker.py` now owns the internal background worker entrypoint through:
  - `BackgroundWorkerRequest`
  - `BackgroundWorkerArgumentError`
  - `parse_argv(...)`
  - `run_background_worker(...)`
  - `main(...)`
- `LocalRunExecutor.start_background(...)` now launches
  `python -m agentnb.runs.worker` instead of routing through the CLI hidden
  `_background-run` command.
- the run-control boundary now owns the worker launch contract explicitly:
  - worker module path and argv shape
  - environment handoff
  - execution-id/project routing
  - completion handoff back into `LocalRunManager.complete_background_run(...)`
- the hidden CLI `_background-run` command was removed.
- `ExecutionService.complete_background_run(...)` was removed so the execution
  boundary no longer exposes a wrapper path for background worker completion.
- internal worker argv validation no longer exits through CLI-style parser
  behavior:
  - worker bootstrap now validates argv explicitly instead of relying on
    `argparse`/`SystemExit`
  - malformed worker requests now degrade into typed run error state when the
    run can be identified instead of silently dying as a detached subprocess
- tests were rebalanced so worker launch and worker-entrypoint behavior are now
  asserted directly at the `runs` boundary rather than through CLI internals.

What this tranche intentionally did not do:

- it did not remove the remaining command-data compatibility adapters
- it did not eliminate all mapping-based output/projection fallbacks
- it did not reshape persisted run state or resource-version policy

What is still incomplete:

- `SerializedCommandData` / `compat_command_data(...)` and some
  mapping-oriented output/projection fallbacks still remain at explicit
  compatibility edges.

## Next Tranche

The next highest-value work is now **removing the remaining explicit
command-data compatibility adapters from internal call paths**. With the worker
boot path now fully under `runs`, the largest remaining abstraction leak is the
mixed typed-vs-mapping response seam around command data, projection, and
output.

That tranche should include:

- making typed command data the only internal currency for app/output/projection
  paths that are already owned by the typed command-data layer.
- removing `SerializedCommandData` / `compat_command_data(...)` from normal
  internal call paths once remaining compatibility callers are retired or
  isolated.
- eliminating mapping-based fallbacks in:
  - `output.py`
  - `projection.py`
  once all app-facing responses are guaranteed to originate from typed command
  data.
- keeping payload `TypedDict`s only at deliberate external/package or serializer
  wire edges rather than as mixed internal currency inside app-facing logic.
- rebalancing tests so typed command-data seams are asserted directly, while any
  remaining compatibility behavior is covered only at explicit external edges.

Advice for that tranche:

- make it another clean cut: once a compatibility adapter stops serving a real
  external edge, remove it rather than preserving it for internal convenience.
- keep the serializer/output boundary explicit: pruning adapters should not
  collapse wire shaping back into `app.py` or other upstream services.
- prefer rebalancing tests toward:
  - `command_data.py`
  - `response_serialization.py`
  - the direct `output.py` / `projection.py` typed seams
  rather than preserving mapping-based fixtures that only restate prior
  implementation structure.

After that, the likely tranche is to revisit whether any remaining mixed
mapping-oriented response helpers still deserve to exist as explicit public
compatibility edges.
