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

### Tranche 8: Typed-Only Response Cutover

Status: done

Completed in the current refactor:

- internal response construction now has one typed path for command responses:
  - `success_response(...)` accepts only typed `command_data`
  - `error_response(...)` accepts typed `command_data` or explicit serialized
    `error_data`
- the old mixed typed-vs-mapping constructor path was removed from the normal
  app-facing flow.
- the remaining command-data compatibility symbols were removed from product
  code:
  - `SerializedCommandData`
  - `CommandDataLike`
  - `ensure_command_data(...)`
  - `compat_command_data(...)`
- `app.py` now carries typed `CommandData` from command handlers to the
  response edge and only uses serialized `error_data` for generic
  non-command-specific error envelopes.
- response/session helpers were narrowed to typed command data:
  - `with_switched_session(...)`
  - run/session lookup resolution in `app.py`
- compatibility-only mapping overloads were removed from typed command-data
  factories that already had typed owning inputs:
  - `DoctorCommandData.from_status(...)`
  - `SessionListEntryData.from_runtime_entry(...)`
  - `SessionDeleteCommandData.from_outcome(...)`
  - `RunCancelCommandData.from_outcome(...)`
- `response_serialization.py`, `projection.py`, `output.py`, and `advice.py`
  now consume typed command data on the normal success path instead of carrying
  mapping fallbacks for internal convenience.
- agent projection now has one explicit rule:
  - if `command_data` exists, project from typed command data
  - otherwise pass through stored serialized `response.data`
  This keeps generic error metadata working without reintroducing mixed
  internal currency.
- tests were rebalanced around typed response builders and typed command-data
  seams; mapping-only constructor coverage now remains only where serialized
  generic error envelopes are still deliberate behavior.

What this tranche intentionally did not do:

- it did not introduce a new typed model for generic error metadata
- it did not remove deliberate external/package helpers that still expose
  serialized payload dicts
- it did not change persistence schemas or the stable response envelope

New invariant after this tranche:

- all successful app-facing responses use typed `CommandData`
- typed command-specific error responses also use typed `CommandData`
- only generic error envelopes are allowed to have `command_data=None`, and
  those carry explicit serialized `error_data` / `ErrorContext` output instead

What is still incomplete:

- some explicit serialized-edge/public helpers still deserve review to confirm
  whether they remain justified as public wire helpers or should be removed
  entirely.

## Next Tranche

### Tranche 9: Ousterhout Audit + Typed Helper / Run View Cutover

Status: done

Completed in the current refactor:

- helper/introspection results now have explicit typed owning models in
  `introspection_models.py` instead of leaking payload `TypedDict`s through
  command data:
  - `VariableEntry`
  - `InspectValue`
  - typed preview models
  - `ReloadResult`
  - `NamespaceDelta`
- `KernelIntrospection` now parses helper JSON directly into those typed helper
  models rather than returning raw mapping payloads on the normal success path.
- app-facing helper command data now carries typed helper models only:
  - `VarsCommandData`
  - `InspectCommandData`
  - `ReloadCommandData`
  - file-exec namespace deltas
- package/root doctor access was narrowed to the typed owner:
  - `KernelRuntime.doctor()` was removed
  - `doctor_status()` is now the owning runtime seam
  - `DoctorStatus` is exported at the package root
- `response_serialization.py` was reduced further toward an edge-only role:
  - serialized helper shaping now converts typed helper models directly
  - shallow convenience exports for run-entry/run-lookup serialization were
    removed
- app/output/advice layers no longer depend on raw helper payload mappings or
  `.payload.get(...)`-style access for vars/inspect/reload/namespace-delta
  behavior.
- run command data was tightened around app-owned DTOs instead of leaking the
  persisted run-store model upward:
  - `RunListEntryData`
  - `RunSnapshotData`
- `app.py` now projects `ExecutionRecord` into those run DTOs once at the app
  boundary, and:
  - `output.py`
  - `advice.py`
  - `response_serialization.py`
  now depend on the app-owned run views rather than on `runs.store`
  projection/default behavior.
- tests were rebalanced around those owning seams:
  - helper-facing tests now construct typed helper results directly
  - run-facing response/output/projection tests now build app-owned run DTOs
    instead of smuggling `ExecutionRecord` through command-data seams
  - CLI fixture helpers now translate legacy shorthand payloads into typed
    helper/run models at the test edge rather than keeping dicts alive in
    product code

What this tranche intentionally did not do:

- it did not restore compatibility for shallow package helpers or agent-output
  field choices when those conflicted with the clean cutover rule
- it did not introduce a new generic typed model for serialized error metadata
- it did not change persisted run schemas or the stable full response envelope
- it did not remove `ExecutionRecord` from exec/reset command data, where the
  execution boundary still legitimately owns the canonical execution outcome

New invariants after this tranche:

- helper/introspection command data is typed-only above the introspection
  boundary
- run lookup/list command data is app-owned view data above the app boundary
- `ExecutionRecord` no longer leaks into app/output/advice/serializer layers
  for `runs list` and `runs show/follow/wait`
- serializer ownership remains at the response edge, but it now consumes typed
  app/helper/run views rather than mixed storage models or ad hoc mappings

What is still incomplete:

- `ExecCommandData` still carries `ExecutionRecord` directly; that is
  defensible because exec/reset are still tightly coupled to the canonical
  execution outcome model, but it remains a seam worth reviewing if the
  execution boundary is split further later.
- `selectors.py` still resolves run selectors against generic run mappings from
  `ExecutionService.list_runs(...)` rather than against a typed run-summary
  view owned by the execution boundary.
- some public/package helpers still expose serialized payload dicts and should
  be reviewed only as deliberate external edges, not as internal convenience
  seams.

### Tranche 10: Typed Run Selection Cutover

Status: done

Completed in the current refactor:

- run selection now has an owning typed seam below selector resolution:
  - `RunSelectionRequest`
  - `RunSelectorCandidate`
  - `ExecutionService.list_run_selector_candidates(...)`
- the actual selector-candidate projection now lives below the execution
  boundary instead of being rebuilt from generic run mappings:
  - `ExecutionStore.read_selector_candidates(...)`
  - `LocalRunManager.list_run_selector_candidates(...)`
- `selectors.py` now resolves `@latest`, `@active`, `@last-error`, and
  `@last-success` against typed `RunSelectorCandidate` values only.
- selector resolution no longer depends on `Mapping`/`cast`/`.get(...)`
  patterns for run snapshots.
- `ExecutionRecord` is no longer a mapping-like compatibility object:
  - `ExecutionRecord.get(...)` removed
  - `ExecutionRecord.__getitem__(...)` removed
- direct `LocalRunManager` tests, app tests, selector tests, and CLI selector
  tests were rebalanced onto the typed selector seam instead of the old
  subscriptable snapshot contract.
- serializer cleanup for this tranche stayed narrow:
  - `_compact_execution_payload(...)` is now private
  - `_compact_inspect_value(...)` is now private
  - tests now assert exec serialization through `serialize_command_data(...)`
    instead of importing private compaction helpers

What this tranche intentionally did not do:

- it did not introduce a narrower exec/reset view DTO
- it did not change persisted run schemas or manifest/resource-version policy
- it did not introduce a generic typed model for serialized error metadata
- it did not collapse the remaining response/projection helper surface into one
  module yet

New invariants after this tranche:

- run selector resolution above the run-control boundary is typed-only
- run-control/storage, not selectors, now own the selector-candidate data seam
- `ExecutionRecord` no longer pretends to be a generic mapping for internal
  convenience
- mapping assertions remain only for deliberate wire/serialized-edge behavior

What is still incomplete:

- `ExecCommandData` still carries `ExecutionRecord` directly, so:
  - `app.py`
  - `output.py`
  - `advice.py`
  - `response_serialization.py`
  still read exec/reset outcome facts from the storage-owned execution model
- selected-output and exec preview derivation are still split across app and
  serializer helpers instead of having one typed owning exec view
- `response_serialization.py` still exports several product-used helpers that
  are legitimate today, but the remaining ownership split between app/output
  and response shaping deserves another clean cut

## Next Tranche

The next highest-value work is now **cutting exec/reset over to an app-owned
exec view and tightening presentation ownership around that view**.

That tranche should include:

- introducing a typed exec/reset view owned above the canonical execution
  outcome model so `ExecCommandData` no longer carries raw `ExecutionRecord`
  into app/output/advice/serializer layers
- projecting from `ExecutionRecord` once at the owning boundary and giving
  higher layers only the facts they actually need:
  - execution identity/status
  - stdout/stderr/result views
  - selected-output text
  - preview data
  - session restart/start metadata
  - namespace delta
- moving selected-output/preview derivation behind one owner instead of
  splitting it between `_ExecPayloadBuilder`, `response_serialization.py`, and
  output/advice helpers
- reviewing the remaining exported response-serialization helpers and keeping
  only the ones that are true edge helpers rather than shared internal
  convenience seams
- keeping tests focused on owning boundaries:
  - direct DTO/projection tests for the new exec view seam
  - app/output/advice tests consuming the typed exec view
  - mapping assertions only for full response serialization and public wire
    contracts

Advice for that tranche:

- keep the clean-cut rule: do not add a dual `record` plus `view` path inside
  `ExecCommandData`
- preserve `ExecutionRecord` as the canonical execution/run-control model, but
  stop leaking it upward where callers only need an app-facing exec view
- prefer one owner for exec preview/selection policy over helper reuse spread
  across layers
- do not widen the tranche into persistence or generic error-model work unless
  a concrete exec-view cutover forces it
