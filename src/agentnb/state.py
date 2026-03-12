from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

STATE_DIR_NAME = ".agentnb"
HISTORY_FILE_NAME = "history.jsonl"
EXECUTIONS_FILE_NAME = "executions.jsonl"
LEGACY_SESSION_FILE_NAME = "session.json"
COMMAND_LOCK_FILE_NAME = "command.lock"
SESSION_FILE_GLOB = "session-*.json"


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
class StateLayout:
    project_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())

    @property
    def state_dir(self) -> Path:
        return self.project_root / STATE_DIR_NAME

    @property
    def history_file(self) -> Path:
        return self.state_dir / HISTORY_FILE_NAME

    @property
    def executions_file(self) -> Path:
        return self.state_dir / EXECUTIONS_FILE_NAME

    @property
    def legacy_session_file(self) -> Path:
        return self.state_dir / LEGACY_SESSION_FILE_NAME

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
