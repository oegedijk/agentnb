# agentnb Roadmap

This roadmap captures planned work **after the current v0.1 baseline**.

## Current Surface

- Project-scoped persistent kernel with explicit session targeting
- Core CLI for lifecycle, execution, history, inspection, and repair flows
- Stable JSON response envelope plus low-noise agent-oriented output defaults
- Structured execution events and durable run records with `execution_id`
- Background execution, live follow, and snapshot-style run inspection on one execution model

## v0.2 - Delivered

v0.2 is complete. It established the current execution-control surface:
- named sessions with ambiguity handling
- first-use execution ergonomics such as `exec --ensure-started` and `status --wait-idle`
- durable run records plus `runs list|show|follow|wait|cancel`
- stable streaming/snapshot semantics and more reliable interrupt/cancel behavior

The rest of this roadmap is forward-looking.

## v0.3 - Reproducibility and Debug Workflows

### Pre-v0.3 Refactors

These refactors should land before the main v0.3 feature surface so replay,
verification, artifacts, and alternate control surfaces build on deeper
abstractions instead of more command-specific glue.

The intent is not just code cleanup. Each refactor below exists to hide a
specific kind of complexity before later roadmap items arrive. If skipped,
that complexity will spread outward into feature code, making the system
harder to extend and the user-facing contract harder to keep stable.

- Unified command journal:
  - purpose: establish one owner for the question "what happened in this session?" across semantic history and persisted execution records
  - hidden complexity to absorb:
    - merging `HistoryStore` records with projected execution records
    - ordering, filtering, and classifying user-visible versus internal commands
    - identifying which commands are replayable versus inspection-only
  - target shape:
    - a `CommandJournal` module that exposes ordered journal entries plus stable selectors (`errors_only`, `include_internal`, `latest`, `last N`, replayable-only)
    - replay, verify, snapshot restore, and export should depend on this layer instead of reading raw history/execution stores directly
  - why this must come first:
    - without a journal, `history`, `replay`, `verify`, snapshot restore, and export will each reimplement slightly different traversal logic
    - this is the deepest current refactor because it turns duplicated read-side logic into one module boundary
  - if skipped:
    - future sessions will add more feature-specific read paths with subtly different filtering and ordering rules
    - user-facing disagreements between `history`, replay selection, and export selection will become likely
  - follow-up work still needed:
    - keep compact/history rendering aligned with journal semantics so internal versus user-visible entries stay distinguishable in `history --all`
- Application service layer above the CLI:
  - purpose: stop `click` command handlers from becoming the de facto application core
  - hidden complexity to absorb:
    - session resolution rules
    - command orchestration and multi-step workflows
    - mapping domain errors into command responses
    - shared request/response handling across CLI, future stdin-JSON mode, and RPC-like control surfaces
  - target shape:
    - an `AgentNBApp` or similarly named application-service layer with typed request/response objects for operations such as `exec`, `history`, `runs`, `snapshot`, `replay`, and `verify`
    - `cli.py` becomes a thin adapter: parse args -> build request -> call app service -> render response
  - why this must come before v0.5 control surfaces:
    - `call` / RPC-like commands and stdin JSON request mode should reuse the same core orchestration as the CLI
    - if skipped, each new control surface will duplicate workflow and error-handling rules
  - if skipped:
    - the CLI file will keep accumulating domain logic and become the place where behavior is defined by accident
    - future non-CLI interfaces will either diverge from CLI behavior or copy large amounts of orchestration code
  - follow-up work still needed:
    - separate transport-neutral operation results from CLI/JSON response envelopes so the app boundary is shaped around operations rather than around command rendering
    - keep CLI-only concerns limited to argument parsing, stdin/file input handling, and human/stream rendering
    - route future non-CLI control surfaces such as snapshot/replay/verify through the same typed request/response seam instead of adding new orchestration paths
- Kernel helper / introspection boundary:
  - purpose: keep inspection, reload, and future debug helpers deep instead of letting large embedded kernel scripts define the architecture
  - hidden complexity to absorb:
    - packaging helper code sent into the kernel for `vars`, `inspect`, `reload`, and future frame/locals/profiling flows
    - parsing structured helper results and mapping them into command-level payloads and history metadata
    - side-effect-aware inspection rules and bounded preview policy for common container and dataframe-like values
  - target shape:
    - a focused helper layer such as `kernel_helpers`, `introspection`, or `debug_ops` with typed helper requests/results and reusable kernel snippets
    - `NotebookOps` should orchestrate high-level operations, not own large inline helper programs plus ad hoc JSON parsing
  - why this must come before richer debugging and safer inspection:
    - frame/locals inspection, bounded previews, profiling, and replay-aware metadata will otherwise accumulate as more one-off embedded scripts inside one shallow module
    - keeping helper code and parsing policy behind one boundary lets inspection behavior deepen without spreading across app, history, and rendering layers
  - if skipped:
    - `ops.py` will keep growing into a mixed bag of orchestration, embedded kernel programs, parsing, and history concerns
    - inspection and debug behavior will be harder to test, reuse, and evolve consistently across future commands
  - first implementation target:
    - extract reusable helper-spec and result-parsing primitives from the current `vars`, `inspect`, and `reload` paths without changing the CLI contract
- Rich execution output model:
  - purpose: preserve execution structure internally so v0.4 artifacts do not have to reverse-engineer text output
  - hidden complexity to absorb:
    - the distinction between streams, expression results, display data, errors, and future artifact references
    - MIME-aware outputs such as text, HTML, tables, images, and richer notebook-style display payloads
  - target shape:
    - a typed execution-output model below rendering, where renderers decide how to present outputs but do not define what outputs exist
    - backend adapters should return structured output items; `output.py` should render or compact them for human/JSON modes
  - why this must come before artifacts:
    - current text flattening is acceptable for `stdout`/`result` but will leak backend limitations into artifact design
    - artifact persistence should consume structured outputs directly rather than scraping text from compacted payloads
  - if skipped:
    - artifact work will either be constrained to plain text or forced to bolt structured meaning back onto flattened output
    - backend, renderer, and persistence changes will be coupled more tightly than they need to be
  - follow-up work still needed:
    - make the structured execution-output model the internal source of truth instead of continuing to treat flat `stdout` / `stderr` / `result` fields as the primary working representation
    - keep transient execution outputs distinct from persisted artifact records so `OutputItem` does not become the accidental artifact model
    - replay/export should read structured outputs as the source of truth; in-flight compatibility snapshots may already project display content into legacy `result`
    - keep renderers and selectors projecting from the structured model instead of growing new text-flattening rules in parallel
- Artifact domain boundary:
  - purpose: define persisted artifacts as a first-class domain concept instead of treating them as saved execution outputs with ad hoc extra metadata
  - hidden complexity to absorb:
    - the distinction between ephemeral execution output and persisted artifacts promoted for later inspection
    - artifact identity, metadata, retention, promotion, and open/list behavior
    - the relationship between output items, artifact references in execution payloads, and artifact records on disk
  - target shape:
    - a separate artifact model such as `ArtifactRef` / `ArtifactRecord` with stable ids, metadata, and lifecycle state
    - execution-output structures may refer to artifacts, but should not become the artifact persistence schema themselves
  - why this must come before v0.4 artifact helpers:
    - `artifacts list|open`, retention controls, and promotion flows need stable persisted artifact identities, not just richer output rendering
    - if artifacts are modeled as a thin extension of `OutputItem`, the first real retention or export workflow will force the boundary to be redrawn
  - if skipped:
    - output rendering, artifact persistence, and artifact lifecycle policy will become too tightly coupled
    - later features such as sharable bundles and backend-specific artifact handling will have to unwind the initial design
  - first implementation target:
    - define artifact references in execution results and a separate persisted artifact record shape before adding artifact CLI commands
- Run manager / execution controller abstraction:
  - status: not started
  - purpose: separate run semantics from the current local subprocess implementation used for background execution
  - hidden complexity to absorb:
    - run identity, observation, waiting, cancellation, timeout handling, and final snapshot semantics
    - the distinction between foreground execution, background execution, and live follow
    - future differences between local, containerized, and remote backends
  - target shape:
    - a `RunManager` or `ExecutionController` abstraction with stable concepts such as `RunHandle`, `RunSnapshot`, `RunObserver`, and capability flags
    - the current `_background-run` subprocess path becomes one implementation detail behind that interface
  - why this must come before alternate backends:
    - local subprocess orchestration should not define the public run contract
    - `runs show|follow|wait|cancel` semantics need to survive backend changes
  - if skipped:
    - the current local background-run mechanism will become the implicit architecture instead of one implementation
    - remote/container backends will require either CLI-visible behavior changes or awkward compatibility shims
  - first implementation target:
    - move background-run spawning and follow/wait/cancel behavior behind one internal interface without changing the CLI contract
  - follow-up work still needed:
    - define run/controller behavior against backend capabilities rather than against the current local IPython backend assumptions
    - keep replay and verify execution flows on the same run-control abstraction instead of giving them their own wait/cancel/progress orchestration paths
    - keep public run semantics defined by the controller contract rather than by the current `_background-run` subprocess behavior
- Backend capability contract:
  - purpose: make future backend variation explicit early so execution, artifacts, and control-plane behavior do not assume every backend matches the local IPython backend
  - hidden complexity to absorb:
    - capability differences such as streaming support, interrupt support, background execution, artifact persistence, and snapshot support
    - backend-specific limitations that need to be surfaced without changing the command contract for local users
    - coordination between backend capabilities, run control, rendering, and future plugin/policy decisions
  - target shape:
    - a typed backend capability object or negotiation contract used by the app layer, run manager, and extension host
    - features should branch on declared capabilities rather than on backend type checks or local-only assumptions
  - why this must come before alternate backends:
    - containerized and remote backends are already planned, and the cost of capability negotiation rises sharply once multiple feature surfaces already assume local behavior
    - a small capability contract now is cheaper than retrofitting one across runs, artifacts, and control-plane operations later
  - if skipped:
    - local backend behavior will become the accidental global contract
    - later backend support will require compatibility shims or user-visible exceptions in places that should have been abstracted
  - first implementation target:
    - define stable capability flags such as `supports_stream`, `supports_background`, `supports_interrupt`, and `supports_artifacts` before adding non-local backend implementations
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
  - if skipped:
    - `.agentnb/` layout knowledge will continue to fragment as new persisted resource types are added
    - schema changes and cleanup logic will become harder to audit and migrate safely
  - follow-up work still needed:
    - give persisted resources stable identities and per-resource schema/version boundaries so future artifacts, exports, and sharable bundles are not defined by local file paths alone
- Internal replay/snapshot planning seam:
  - purpose: give future snapshot, replay, and verify features typed planning interfaces without shipping partial feature behavior
  - hidden complexity to absorb:
    - deriving replay-ready steps from journal selections
    - centralizing future snapshot resource planning against the state repository boundary
    - keeping feature code off raw journal/store payloads
  - target shape:
    - internal planner types such as replay steps/plans and snapshot resource plans, with no public CLI or execution behavior yet
    - future replay/verify/snapshot implementations should consume these plans rather than rebuilding selection/layout logic themselves
  - why this must come before the feature surface:
    - it keeps the first replay/snapshot implementation from encoding selection and storage policy ad hoc in command handlers
    - it gives the journal and state repository abstractions one consumer before user-visible features arrive
  - if skipped:
    - replay and snapshot features will likely bind directly to raw journal/state details, weakening the new abstraction boundaries immediately
  - follow-up work still needed:
    - connect replay execution and snapshot persistence to these planners once the user-visible feature work starts
- Unified command recording / journal write path:
  - purpose: give semantic command recording one owner so new history metadata and replay provenance do not drift between stores
  - hidden complexity to absorb:
    - recording user-visible commands versus internal helper executions consistently across `HistoryStore` and `ExecutionStore`
    - attaching command metadata such as labels, tags, execution mode, replay provenance, and future verify diagnostics
    - keeping write-time semantics aligned with the `CommandJournal` read model so `history`, replay selection, and export stay consistent
  - target shape:
    - a `CommandRecorder`, `JournalWriter`, or equivalent boundary that owns how semantic command records and execution-linked history entries are emitted
    - `NotebookOps` and `ExecutionService` should call this boundary instead of each constructing command records independently
  - why this must come before replay/verify metadata expansion:
    - replay, verify, and richer history queries need stable write-time metadata, not just a stronger read-side merger
    - without a single recorder, future fields such as tags or execution mode will likely appear in one path first and lag in others
  - if skipped:
    - history semantics will continue to be reconstructed from partially duplicated write paths
    - new metadata fields will be more likely to disagree between interactive ops, foreground exec, background exec, and future replay flows
  - first implementation target:
    - centralize construction of semantic command records for `exec`, `reset`, `vars`, `inspect`, and `reload` while preserving the existing history and runs contract

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
  - richer history metadata (`tags`, labels, execution mode)
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

- History entries gain optional `tags`, `command_type`, and `execution_id`.
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
