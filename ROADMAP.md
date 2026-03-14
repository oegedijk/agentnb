# agentnb Roadmap

This roadmap captures planned work **after the current v0.1 baseline**.

## Current Surface

- Project-scoped persistent kernel with explicit session targeting
- Core CLI for lifecycle, execution, history, inspection, and repair flows
- Stable JSON response envelope plus low-noise agent-oriented output defaults
- Structured execution events and durable run records with `execution_id`
- Background execution, live follow, and snapshot-style run inspection on one execution model
- Unified command journal read path over semantic history and persisted runs
- Application service layer with typed request/response seams under the CLI
- Dedicated introspection boundary for `vars`, `inspect`, and `reload`
- Typed public payloads through app/output plus typed Jupyter message translation at the backend edge

## v0.2 - Delivered

v0.2 is complete. It established the current execution-control surface:
- named sessions with ambiguity handling
- first-use execution ergonomics such as `exec --ensure-started` and `status --wait-idle`
- durable run records plus `runs list|show|follow|wait|cancel`
- stable streaming/snapshot semantics and more reliable interrupt/cancel behavior

The rest of this roadmap is forward-looking.

## v0.3 - Reproducibility and Debug Workflows

### Pre-v0.3 Refactors

Completed foundations:

- Unified journal + recording boundaries now own semantic read/write flow for history and executions.
- `AgentNBApp`, typed payloads, and typed Jupyter translation now define the app/output boundary cleanly.
- `introspection.py` owns helper execution and typed parsing for `vars`, `inspect`, and `reload`.
- Kernel/backend code now lives behind `agentnb.kernel`, and run orchestration behind `RunManager`.
- Structured execution output is now the internal source of truth, with flat `stdout` / `stderr` / `result` preserved as compatibility projections at the boundary.
- Backend capability checks now branch on a typed capability contract instead of local-backend assumptions.
- Session/kernel state paths and canonical session identity now flow through `StateRepository` / `SessionStateFiles` instead of being recomputed across runtime and backend layers.
- Run records now persist explicit cancel provenance and expose stable cancellation metadata across `runs show|list` and failed-only reads.
- The test suite now has cleaner fixtures, broader behavioral/type coverage, real CLI smoke coverage, and `ty` over both `src` and `tests`.

Remaining prep refactors:
- Artifact domain boundary:
  - purpose: separate persisted artifacts from transient execution outputs before artifact commands exist
  - remaining gap:
    - there is still no first-class persisted artifact model with stable ids, metadata, and lifecycle state
- Run-control follow-up:
  - keep replay and verify execution flows on the same run-control abstraction instead of giving them their own wait/cancel/progress orchestration paths
  - keep public run semantics defined by the controller contract rather than by the current local subprocess behavior
- Backend capability follow-up:
  - grow the minimal capability contract into the app/run-control/extension boundary before adding non-local backends
  - keep features branching on declared capabilities rather than on backend type checks or local-only assumptions
- Extension host boundary:
  - purpose: give plugins, policy, and reliability hooks one deep home instead of growing ad hoc methods across runtime and CLI layers
  - hidden complexity to absorb:
    - plugin registration and lifecycle
    - policy evaluation before/after execution
    - execution context passed to extensions
    - extension failure isolation and stable error reporting
  - target shape:
    - an `ExtensionHost` or equivalent boundary that owns loading, ordering, and invoking extensions/policies
    - current no-op hooks become a narrow compatibility shim or are subsumed into the extension host
  - why this must come before v0.5 policy/plugins:
    - otherwise policy decisions will leak into `KernelRuntime`, CLI handlers, and backend code as one-off special cases
    - reliability features such as diagnostics or restart hooks will also need one place to attach
  - if skipped:
    - every new policy or plugin-style feature will add another ad hoc callback or special-case branch
    - extension failures will be harder to isolate and reason about because there will be no single host boundary
  - first implementation target:
    - define typed execution lifecycle events and extension context objects before adding actual plugin loading
  - follow-up work still needed:
    - keep extension APIs event/context-based rather than growing a method-per-hook surface that mirrors current runtime internals
    - avoid exposing raw backend or runtime objects directly to extensions so the first plugin API does not freeze internal implementation details
- State layout ownership:
  - purpose: centralize ownership of `.agentnb/` filesystem layout, schema versions, and migration boundaries
  - hidden complexity to absorb:
    - path naming and discovery for sessions, histories, runs, snapshots, artifacts, and future metadata
    - schema versioning and compatibility checks
    - cleanup and retention rules for persisted state
  - target shape:
    - one state-layout module or state repository boundary that defines where each persisted resource lives and how schema versions are tracked
    - leaf modules should ask for their paths/resources instead of encoding layout conventions independently
  - why this must come before snapshots and artifacts:
    - snapshots and artifacts will otherwise repeat the current pattern of each module knowing its own filenames and directory structure
    - centralized ownership will make future migrations less risky
  - follow-up work still needed:
    - give persisted resources stable identities and per-resource schema/version boundaries so future artifacts, exports, and sharable bundles are not defined by local file paths alone
### Internal Planning Seams

- Internal replay/snapshot planning seam:
  - purpose: give future snapshot, replay, and verify features typed planning interfaces without shipping partial feature behavior
  - target shape:
    - internal planner types such as replay steps/plans and snapshot resource plans
    - future replay/verify/snapshot implementations should consume these plans rather than rebuilding journal/state selection logic ad hoc

### Goals

- Make iterative agent work easier to replay, diagnose, and promote to tests.
- Make "clean verification" a first-class workflow instead of a manual sequence of commands.
- Make iterative CLI use lower-noise and more obvious without requiring full JSON output.
- Improve recovery and next-step guidance while keeping the command surface small and composable.

### Planned Features

- Session snapshots:
  - `agentnb snapshot create|list|restore`
- Replay/export:
  - replay history to new session
  - `agentnb replay --to-session <name>`
  - `agentnb verify` to restart a clean session and replay selected history or snapshot state
  - export to `.ipynb` and markdown transcript
- Better debugging:
  - traceback enrichment
  - frame/locals inspection commands
  - optional profiling (`cProfile`) command paths
- Safer inspection:
  - bounded previews for large values
  - structured previews for common containers (`list`, `dict`, `tuple`, dataframe-like objects)
  - side-effect-aware inspection paths that avoid arbitrary `repr(...)` when possible
  - richer history metadata beyond the current journal shape (`tags`, execution mode, replay/verify provenance)
- History/query ergonomics:
  - clearer failed-only flows
  - optional flat JSON output for history-oriented shell pipelines
  - direct selectors for the most recent failed or successful execution
- Output shaping:
  - additional low-noise modes beyond the current `--quiet` and `--no-suggestions`
  - a compact working-output mode distinct from full `--json`
  - clearer separation between interactive working output and exact machine-contract output
  - more state-aware, recovery-oriented suggestions within the existing command set
  - more actionable `SESSION_BUSY` and `AMBIGUOUS_SESSION` responses that surface the shortest resolving path directly

### API/Contract Notes

- History entries should grow optional `tags` and execution-mode/provenance metadata on top of the current `command_type` and `execution_id` fields.
- Verification responses should identify the first failed step and the source execution that produced it.
- JSON envelopes should keep machine-stable fields predictable across commands (`session_id`, `execution_id`, `duration_ms`, typed error codes).
- Keep full `--json` as the exact machine-stable contract rather than assuming it is the best default working mode.
- Prefer improving behavior, flags, suggestions, and output shaping of existing commands over adding new top-level commands.
- Snapshot metadata tracked in `.agentnb/` with schema versioning.

## v0.4 - Rich Output and Artifacts

### Goals

- Improve non-text outputs for data-heavy workflows.
- Clarify which execution outputs are ephemeral versus persisted for later inspection.

### Planned Features

- Structured artifacts:
  - tables, plots, HTML snippets, files
- Execution responses include `artifacts` list in JSON mode.
- CLI helpers:
  - `agentnb artifacts list`
  - `agentnb artifacts open <id>`
- Output persistence controls:
  - recorded versus ephemeral execution modes
  - artifact retention policy and cleanup commands
  - optional promotion of prior execution results into saved artifacts
- Agent-oriented output presets:
  - deterministic output flags such as `--no-color` and `--no-suggestions`
  - an `--agent` preset for machine-oriented defaults (`--json` plus deterministic output rules)
  - shell/jq-oriented examples in help and docs for common extraction patterns

### API/Contract Notes

- Extend result schema with backward-compatible `artifacts` field.
- Keep plain `stdout`/`result` contract intact.

## v0.5 - Extensibility, Policy, and Reliability

### Goals

- Turn internal seams into stable extension points.
- Improve production reliability for long-lived agent usage.

### Planned Features

- Plugin interface:
  - custom operations / renderers
  - lifecycle hook registration
- Policy hooks:
  - pre/post execute checks
  - optional allow/deny rules
- Reliability:
  - kernel auto-restart on crash (opt-in)
  - health checks + structured diagnostics
  - improved cleanup for stale state
- Alternate control surfaces:
  - a uniform `call` / RPC-like command shape over existing operations
  - stdin JSON request mode for tool wrappers and long-lived agent adapters

### API/Contract Notes

- Versioned plugin API surface.
- Policy violations return stable, typed error codes.

## v0.6+ - Runtime Backends and Collaboration

### Goals

- Decouple from local-only execution while keeping CLI contract stable.

### Planned Features

- Alternate backends:
  - containerized local backend
  - remote backend connector
- Collaboration and CI modes:
  - headless CI run mode
  - import/export sharable session bundles

### API/Contract Notes

- Backend capability negotiation (`supports_stream`, `supports_artifacts`, etc.).
- Command behavior remains compatible for local backend users.

## Cross-Cutting Work

- Documentation upgrades:
  - troubleshooting matrix by platform
  - “agent integration” examples for CLI-first tools
  - examples optimized for machine consumers (`jq`, tool wrappers, low-noise output)
  - maintain an agent-focused smoke-scenario catalog for deep iterative workflows
- Contract hardening:
  - schema regression tests
  - explicit deprecation policy for JSON fields
- Performance:
  - benchmark startup latency, round-trip execution latency, and memory overhead
- Output/noise control:
  - keep machine-oriented modes predictable during streaming and control-plane errors
- Command-surface discipline:
  - prefer a small set of composable commands over feature-specific command growth
  - optimize common workflows by improving defaults, suggestions, and output shaping before introducing new verbs

## Near-Term Priority Queue

1. Replay/export (history -> notebook/transcript)
2. Snapshot workflows (`snapshot create|list|restore`)
3. Verification/replay workflows (`replay`, `verify`)
