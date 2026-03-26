from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .state import (
    _SESSION_RECORD_FILE_PATTERN,
    _STATE_RESOURCES,
    EXECUTIONS_FILE_NAME,
    HISTORY_FILE_NAME,
    LEGACY_SESSION_FILE_NAME,
    SESSION_PREFERENCES_FILE_NAME,
    STATE_DIR_NAME,
    STATE_MANIFEST_FILE_NAME,
    SessionStateFiles,
    StateResource,
    command_lock_file,
    kernel_connection_file,
    kernel_log_file,
    session_file_name,
)


@dataclass(slots=True, frozen=True)
class StateLayout:
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
        return self.state_dir / HISTORY_FILE_NAME

    @property
    def executions_file(self) -> Path:
        return self.state_dir / EXECUTIONS_FILE_NAME

    @property
    def legacy_session_file(self) -> Path:
        return self.state_dir / LEGACY_SESSION_FILE_NAME

    @property
    def session_preferences_file(self) -> Path:
        return self.state_dir / SESSION_PREFERENCES_FILE_NAME

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

    def session_runtime(self, session_id: str) -> SessionStateFiles:
        state_dir = self.state_dir
        return SessionStateFiles(
            session_id=session_id,
            state_dir=state_dir,
            session_record=state_dir / session_file_name(session_id),
            legacy_session_record=self.legacy_session_file,
            connection_file=kernel_connection_file(state_dir, session_id),
            log_file=kernel_log_file(state_dir, session_id),
            command_lock_file=command_lock_file(state_dir, session_id),
        )

    def session_state(self, session_id: str) -> SessionStateFiles:
        return self.session_runtime(session_id)

    def session_file(self, session_id: str) -> Path:
        return self.session_runtime(session_id).session_record

    def connection_file(self, session_id: str) -> Path:
        return self.session_runtime(session_id).connection_file

    def log_file(self, session_id: str) -> Path:
        return self.session_runtime(session_id).log_file

    def command_lock_file(self, session_id: str) -> Path:
        return self.session_runtime(session_id).command_lock_file

    def session_files(self) -> list[Path]:
        if not self.state_dir.exists():
            return []
        return sorted(
            path
            for path in self.state_dir.iterdir()
            if path.is_file() and _SESSION_RECORD_FILE_PATTERN.fullmatch(path.name)
        )

    def resource(self, name: str) -> StateResource:
        try:
            return _STATE_RESOURCES[name]
        except KeyError as exc:
            raise KeyError(f"Unknown state resource: {name}") from exc

    def resource_path(self, name: str) -> Path:
        return self.resource(name).resolve(self.state_dir)

    def resources(self) -> dict[str, StateResource]:
        return dict(_STATE_RESOURCES)
