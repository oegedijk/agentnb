# agentnb Roadmap

This roadmap captures planned work **after the current v0.1 baseline**.

## Current Baseline (Done)

- Project-scoped persistent kernel
- CLI: `start`, `stop`, `status`, `exec`, `interrupt`, `reset`, `vars`, `inspect`, `reload`, `history`, `doctor`
- JSON response envelope with stable top-level fields
- Provisioning flow with interpreter selection + `ipykernel` auto-install
- Pytest/ruff/ty CI quality gates

## v0.2 - Session and Execution Ergonomics

### Goals

- Support multiple sessions per project without breaking default behavior.
- Improve execution control for long-running workflows.
- Make session targeting explicit and safe when multiple live contexts exist.
- Introduce a structured execution model that can support streaming and background runs.

### Planned Features

- Named sessions:
  - `--session <name>` across all kernel-dependent commands
  - `agentnb sessions list`, `agentnb sessions attach`, `agentnb sessions delete`
  - explicit ambiguity errors when multiple sessions exist and no target is provided
  - session metadata in listings (status, age, interpreter, last activity)
- First-use execution ergonomics:
  - `agentnb exec --ensure-started` to auto-start a missing kernel for the default workflow
  - `status --wait [--timeout]` to block until a kernel is ready for execution
  - `--session` aliases that are short and consistent across commands
- Background execution:
  - `agentnb exec --background` returning `execution_id`
  - `agentnb wait <execution_id>`, `agentnb cancel <execution_id>`
- Streaming option:
  - `agentnb exec --stream` for incremental stdout/stderr updates
- Execution event model:
  - typed events for `stdout`, `stderr`, `result`, `display`, `error`, `status`
  - stable `execution_id` across foreground, streaming, and background execution paths
  - internal event persistence to support replay, export, and artifact capture later

### API/Contract Notes

- Add `session_id` and `execution_id` consistently to execution payloads.
- Add an event schema that remains stable across sync and streaming modes.
- Support top-level output-mode defaults so agents do not need to repeat `--json` on every command.
- Keep existing `default` session behavior unchanged.

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
  - `history --latest`, `history --last N`, and clearer failed-only flows
  - optional flat JSON output for history-oriented shell pipelines
  - direct selectors for the most recent failed or successful execution
- Output shaping:
  - `exec --stdout-only`, `--stderr-only`, and `--result-only` for script-friendly capture
  - `--quiet` and `--no-suggestions` modes for reduced prose in human output

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
- Contract hardening:
  - schema regression tests
  - explicit deprecation policy for JSON fields
- Performance:
  - benchmark startup latency, round-trip execution latency, and memory overhead

## Near-Term Priority Queue

1. Multi-session support (`--session`, list/attach/delete)
2. Background execution IDs + cancel/wait commands
3. Replay/export (history -> notebook/transcript)
4. Artifact channel in JSON output
5. Plugin API stabilization (hooks + op registry)
