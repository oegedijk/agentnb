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

Status as of March 11, 2026:
- completed: named sessions, ambiguity handling, `exec --ensure-started`, `status --wait`
- completed: persisted execution records with `execution_id`
- completed: background execution with `runs list|show|wait|cancel`
- completed: real-time streaming execution on top of the same execution model
- completed: foreground interrupt reliability, active-execution `status`, consistent session `last_activity`, `status --wait-idle`, and live `runs follow`
- completed: explicit cancel semantics plus a clear snapshot/live split between `runs show` and `runs follow`
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

### Goals

- Make iterative agent work easier to replay, diagnose, and promote to tests.
- Make "clean verification" a first-class workflow instead of a manual sequence of commands.

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

### API/Contract Notes

- History entries gain optional `tags`, `command_type`, and `execution_id`.
- Verification responses should identify the first failed step and the source execution that produced it.
- JSON envelopes should keep machine-stable fields predictable across commands (`session_id`, `execution_id`, `duration_ms`, typed error codes).
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

## Near-Term Priority Queue

1. Replay/export (history -> notebook/transcript)
2. Snapshot workflows (`snapshot create|list|restore`)
3. Verification/replay workflows (`replay`, `verify`)
