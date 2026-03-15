from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .journal import JournalEntry, JournalSelection
from .state import StateManifest, StateRepository, StateResource


@dataclass(slots=True, frozen=True)
class ReplayPlanStep:
    execution_id: str
    session_id: str
    command_type: str
    code: str | None
    ts: str
    provenance_source: str
    provenance_detail: str


@dataclass(slots=True, frozen=True)
class ReplayPlan:
    steps: list[ReplayPlanStep]


class ReplayPlanner:
    def build(self, selection: JournalSelection) -> ReplayPlan:
        steps: list[ReplayPlanStep] = []
        for entry in selection.entries:
            if not _is_replay_step(entry):
                continue
            if entry.execution_id is None:
                continue
            steps.append(
                ReplayPlanStep(
                    execution_id=entry.execution_id,
                    session_id=entry.session_id,
                    command_type=entry.command_type,
                    code=entry.code,
                    ts=entry.ts,
                    provenance_source=entry.provenance_source,
                    provenance_detail=entry.provenance_detail,
                )
            )
        return ReplayPlan(steps=steps)


@dataclass(slots=True, frozen=True)
class SnapshotResourcePlan:
    manifest: StateManifest
    resources: list[StateResource]

    def resource_paths(self, repository: StateRepository) -> list[Path]:
        return [resource.resolve(repository.state_dir) for resource in self.resources]


class SnapshotPlanner:
    def build(self, repository: StateRepository) -> SnapshotResourcePlan:
        manifest = repository.ensure_compatible()
        resources = list(repository.snapshot_resources())
        return SnapshotResourcePlan(manifest=manifest, resources=resources)


def _is_replay_step(entry: JournalEntry) -> bool:
    return entry.classification == "replayable"
