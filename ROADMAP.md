# agentnb Roadmap

This roadmap captures planned work **after the current v0.1 baseline**.

## Current Baseline (Done)

- Project-scoped persistent kernel
- CLI: `start`, `stop`, `status`, `exec`, `interrupt`, `reset`, `vars`, `inspect`, `reload`, `history`, `doctor`
- JSON response envelope with stable top-level fields
- Provisioning flow with interpreter selection + `ipykernel` auto-install
- Top-level output defaults: `--agent`, `--json`, `--quiet`, `--no-suggestions`
- Script-friendly output selectors: `exec --stdout-only`, `--stderr-only`, `--result-only`
- History query shortcuts: `history --latest`, `history --last N`
- Pytest/ruff/ty CI quality gates

## v0.2 - Session and Execution Ergonomics

Status as of March 12, 2026:
- completed: named sessions, ambiguity handling, `exec --ensure-started`, `status --wait`
- completed: persisted execution records with `execution_id`
- completed: background execution with `runs list|show|wait|cancel`
- completed: real-time streaming execution on top of the same execution model
- completed: foreground interrupt reliability, active-execution `status`, consistent session `last_activity`, `status --wait-idle`, and live `runs follow`
- completed: explicit cancel semantics plus a clear snapshot/live split between `runs show` and `runs follow`
- completed: `runs cancel` now preserves the run's natural terminal state when completion wins the race against cancellation, instead of always overwriting it with synthetic cancellation
- v0.2 status: complete

### Goals

- Support multiple sessions per project without breaking default behavior.
- Improve execution control for long-running workflows.
- Make session targeting explicit and safe when multiple live contexts exist.
- Introduce a structured execution model that can support streaming and background runs.

### Planned Features

- Named sessions:
  - `--session <name>` across all kernel-dependent commands
  - `agentnb sessions list`, `agentnb sessions delete`
  - optional `agentnb sessions attach` only after the target/default-session UX is specified
  - explicit ambiguity errors when multiple sessions exist and no target is provided
  - session metadata in listings (status, age, interpreter, last activity)
- First-use execution ergonomics:
  - `agentnb exec --ensure-started` to auto-start a missing kernel for the default workflow
  - `status --wait [--timeout]` to block until a kernel is ready for execution
  - `status --wait-idle [--timeout]` or equivalent to block until a session is safe for the next command
  - `--session` aliases that are short and consistent across commands
- Execution event model:
  - typed events for `stdout`, `stderr`, `result`, `display`, `error`, `status`
  - stable `execution_id` across foreground, streaming, and background execution paths
  - internal event persistence to support replay, export, and artifact capture later
- Execution control stabilization:
  - foreground `interrupt` must reliably reach a running execution
  - `status` must accurately report live-versus-not-ready state while commands are in flight
  - session listings should reflect recent execution activity consistently
  - completed: cancellation reports whether the session was preserved or stopped
- Run observation ergonomics:
  - completed: live follow for background runs on top of the persisted event model
  - completed: `runs show` is a snapshot view and `runs follow` is the live observation path

### Delivery Order

1. Completed: expose the existing session model in the CLI with `--session` while preserving `default`.
2. Completed: add session discovery/deletion commands and ambiguity handling when multiple live sessions exist.
3. Completed: add `exec --ensure-started` and `status --wait`.
4. Completed: land the execution event schema and persisted execution records.
5. Completed: land real-time streaming execution on top of the same execution model.
6. Completed: clarify cancel semantics and session lifecycle after cancellation.
7. Completed: tighten `runs show` versus `runs follow` so snapshot and live observation stay distinct.

### API/Contract Notes

- `session_id` is already present in top-level command envelopes; add `execution_id` consistently to execution payloads.
- Extend the event schema to cover sync, streaming, and replay modes without changing event meaning by mode.
- Support top-level output-mode defaults so agents do not need to repeat `--json` on every command.
- Keep existing `default` session behavior unchanged.
- Control-plane commands need stable semantics during active execution, especially `status`, `interrupt`, and `cancel`.

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
  - status: completed initial version
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
    - add selection helpers for common replay/verify inputs
    - add provenance fields only when replay/verify actually need them
    - keep compact/history rendering aligned with journal semantics so internal versus user-visible entries stay distinguishable in `history --all`
- Application service layer above the CLI:
  - status: current CLI workflows migrated onto the app boundary
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
    - keep CLI-only concerns limited to argument parsing, stdin/file input handling, and human/stream rendering
    - route future non-CLI control surfaces such as snapshot/replay/verify through the same typed request/response seam instead of adding new orchestration paths
- Rich execution output model:
  - status: initial slice landed
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
  - first implementation target:
    - completed: preserve display/result separation and MIME metadata in the internal execution-output path while keeping the current human/JSON contract stable
  - follow-up work still needed:
    - move background progress persistence onto the same structured-output projection path used by foreground execution
    - persist structured outputs in execution records so replay/export do not have to reconstruct them from compacted text fields
    - keep renderers and selectors projecting from the structured model instead of growing new text-flattening rules in parallel
- Run manager / execution controller abstraction:
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
- State layout ownership:
  - status: completed initial path/layout extraction
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
  - first implementation target:
    - completed: extract `.agentnb/` layout constants and path-building rules into one module before adding new persisted resource types
  - follow-up work still needed:
    - move schema-version ownership and compatibility checks into the same boundary
    - route future snapshot/artifact path registration through the state-layout module instead of adding new ad hoc filenames

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
