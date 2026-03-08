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

### Planned Features

- Named sessions:
  - `--session <name>` across all kernel-dependent commands
  - `agentnb sessions list`, `agentnb sessions attach`, `agentnb sessions delete`
- Background execution:
  - `agentnb exec --background` returning `execution_id`
  - `agentnb wait <execution_id>`, `agentnb cancel <execution_id>`
- Streaming option:
  - `agentnb exec --stream` for incremental stdout/stderr updates

### API/Contract Notes

- Add `session_id` and `execution_id` consistently to execution payloads.
- Keep existing `default` session behavior unchanged.

## v0.3 - Reproducibility and Debug Workflows

### Goals

- Make iterative agent work easier to replay, diagnose, and promote to tests.

### Planned Features

- Session snapshots:
  - `agentnb snapshot create|list|restore`
- Replay/export:
  - replay history to new session
  - export to `.ipynb` and markdown transcript
- Better debugging:
  - traceback enrichment
  - frame/locals inspection commands
  - optional profiling (`cProfile`) command paths

### API/Contract Notes

- History entries gain optional `tags`, `command_type`, and `execution_id`.
- Snapshot metadata tracked in `.agentnb/` with schema versioning.

## v0.4 - Rich Output and Artifacts

### Goals

- Improve non-text outputs for data-heavy workflows.

### Planned Features

- Structured artifacts:
  - tables, plots, HTML snippets, files
- Execution responses include `artifacts` list in JSON mode.
- CLI helpers:
  - `agentnb artifacts list`
  - `agentnb artifacts open <id>`

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
