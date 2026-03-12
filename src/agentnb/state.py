from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .errors import StateCompatibilityError

STATE_DIR_NAME = ".agentnb"
STATE_MANIFEST_FILE_NAME = "state-manifest.json"
STATE_SCHEMA_VERSION = "1"
HISTORY_FILE_NAME = "history.jsonl"
EXECUTIONS_FILE_NAME = "executions.jsonl"
LEGACY_SESSION_FILE_NAME = "session.json"
COMMAND_LOCK_FILE_NAME = "command.lock"
SESSION_FILE_GLOB = "session-*.json"

ResourceKind = Literal["file", "directory"]


@dataclass(slots=True, frozen=True)
class StateResource:
    name: str
    kind: ResourceKind
    relative_path: Path

    def resolve(self, state_dir: Path) -> Path:
        return state_dir / self.relative_path


@dataclass(slots=True, frozen=True)
class StateManifest:
    schema_version: str = STATE_SCHEMA_VERSION
    resource_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"schema_version": self.schema_version}
        if self.resource_versions:
            payload["resource_versions"] = dict(self.resource_versions)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StateManifest:
        schema_version = payload.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise ValueError("Missing schema_version")

        raw_versions = payload.get("resource_versions", {})
        resource_versions: dict[str, str] = {}
        if isinstance(raw_versions, dict):
            for key, value in raw_versions.items():
                if isinstance(key, str) and isinstance(value, str):
                    resource_versions[key] = value

        return cls(schema_version=schema_version, resource_versions=resource_versions)


def session_file_name(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"session-{digest}.json"


def kernel_connection_file(state_dir: Path, session_id: str) -> Path:
    return state_dir / f"kernel-{session_id}.json"


def kernel_log_file(state_dir: Path, session_id: str) -> Path:
    return state_dir / f"kernel-{session_id}.log"


def command_lock_file(state_dir: Path, session_id: str) -> Path:
    return state_dir / f"{COMMAND_LOCK_FILE_NAME}-{session_id}"


@dataclass(slots=True, frozen=True)
class StateRepository:
    project_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())

    @property
    def state_dir(self) -> Path:
        return self.project_root / STATE_DIR_NAME

    @property
    def manifest_file(self) -> Path:
        return self.state_dir / STATE_MANIFEST_FILE_NAME

    @property
    def history_file(self) -> Path:
        return self.resource_path("history")

    @property
    def executions_file(self) -> Path:
        return self.resource_path("executions")

    @property
    def legacy_session_file(self) -> Path:
        return self.resource_path("legacy_session")

    @property
    def snapshots_dir(self) -> Path:
        return self.resource_path("snapshots")

    @property
    def artifacts_dir(self) -> Path:
        return self.resource_path("artifacts")

    @property
    def exports_dir(self) -> Path:
        return self.resource_path("exports")

    @property
    def metadata_dir(self) -> Path:
        return self.resource_path("metadata")

    def session_file(self, session_id: str) -> Path:
        return self.state_dir / session_file_name(session_id)

    def connection_file(self, session_id: str) -> Path:
        return kernel_connection_file(self.state_dir, session_id)

    def log_file(self, session_id: str) -> Path:
        return kernel_log_file(self.state_dir, session_id)

    def command_lock_file(self, session_id: str) -> Path:
        return command_lock_file(self.state_dir, session_id)

    def session_files(self) -> list[Path]:
        if not self.state_dir.exists():
            return []
        return sorted(self.state_dir.glob(SESSION_FILE_GLOB))

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def ensure_gitignore_entry(self) -> bool:
        self.ensure_state_dir()
        gitignore = self.project_root / ".gitignore"
        entry = f"{STATE_DIR_NAME}/"

        if not gitignore.exists():
            gitignore.write_text(f"{entry}\n", encoding="utf-8")
            return True

        existing = gitignore.read_text(encoding="utf-8")
        lines = existing.splitlines()
        if entry in lines:
            return False

        suffix = "" if existing.endswith("\n") else "\n"
        with gitignore.open("a", encoding="utf-8") as handle:
            handle.write(f"{suffix}{entry}\n")
        return True

    def manifest(self) -> StateManifest:
        if not self.manifest_file.exists():
            return StateManifest()
        try:
            payload = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateCompatibilityError(
                "State manifest is unreadable.",
                data={"manifest_file": str(self.manifest_file)},
            ) from exc
        if not isinstance(payload, dict):
            raise StateCompatibilityError(
                "State manifest has an invalid shape.",
                data={"manifest_file": str(self.manifest_file)},
            )
        try:
            manifest = StateManifest.from_dict(payload)
        except ValueError as exc:
            raise StateCompatibilityError(
                "State manifest is missing required fields.",
                data={"manifest_file": str(self.manifest_file)},
            ) from exc
        self.validate_manifest(manifest)
        return manifest

    def save_manifest(self, manifest: StateManifest) -> None:
        self.ensure_state_dir()
        self.manifest_file.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=True),
            encoding="utf-8",
        )

    def validate_manifest(self, manifest: StateManifest) -> None:
        if manifest.schema_version != STATE_SCHEMA_VERSION:
            raise StateCompatibilityError(
                (
                    f"State schema {manifest.schema_version} is incompatible with "
                    f"agentnb schema {STATE_SCHEMA_VERSION}."
                ),
                data={
                    "manifest_schema_version": manifest.schema_version,
                    "supported_schema_version": STATE_SCHEMA_VERSION,
                },
            )

        known_resources = set(self.resources())
        unknown_resources = sorted(set(manifest.resource_versions) - known_resources)
        if unknown_resources:
            raise StateCompatibilityError(
                "State manifest references unknown resources.",
                data={"unknown_resources": unknown_resources},
            )

    def ensure_compatible(self) -> StateManifest:
        return self.manifest()

    def resource(self, name: str) -> StateResource:
        try:
            return _STATE_RESOURCES[name]
        except KeyError as exc:
            raise KeyError(f"Unknown state resource: {name}") from exc

    def resource_path(self, name: str) -> Path:
        return self.resource(name).resolve(self.state_dir)

    def resources(self) -> dict[str, StateResource]:
        return dict(_STATE_RESOURCES)


@dataclass(slots=True, frozen=True)
class StateLayout(StateRepository):
    pass


_STATE_RESOURCES: dict[str, StateResource] = {
    "history": StateResource(
        name="history",
        kind="file",
        relative_path=Path(HISTORY_FILE_NAME),
    ),
    "executions": StateResource(
        name="executions",
        kind="file",
        relative_path=Path(EXECUTIONS_FILE_NAME),
    ),
    "legacy_session": StateResource(
        name="legacy_session",
        kind="file",
        relative_path=Path(LEGACY_SESSION_FILE_NAME),
    ),
    "snapshots": StateResource(
        name="snapshots",
        kind="directory",
        relative_path=Path("snapshots"),
    ),
    "artifacts": StateResource(
        name="artifacts",
        kind="directory",
        relative_path=Path("artifacts"),
    ),
    "exports": StateResource(
        name="exports",
        kind="directory",
        relative_path=Path("exports"),
    ),
    "metadata": StateResource(
        name="metadata",
        kind="directory",
        relative_path=Path("metadata"),
    ),
}
