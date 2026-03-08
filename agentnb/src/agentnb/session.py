from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_SESSION_ID = "default"
STATE_DIR_NAME = ".agentnb"
SESSION_FILE_NAME = "session.json"
HISTORY_FILE_NAME = "history.jsonl"


@dataclass(slots=True)
class SessionInfo:
    session_id: str
    pid: int
    connection_file: str
    python_executable: str
    project_root: str
    started_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionInfo:
        return cls(
            session_id=str(payload["session_id"]),
            pid=int(payload["pid"]),
            connection_file=str(payload["connection_file"]),
            python_executable=str(payload["python_executable"]),
            project_root=str(payload["project_root"]),
            started_at=str(payload["started_at"]),
        )


def resolve_project_root(cwd: Path | None = None, override: Path | None = None) -> Path:
    if override is not None:
        return override.expanduser().resolve()

    current = (cwd or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists():
            return candidate
    return current


def pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class SessionStore:
    def __init__(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.project_root = project_root.resolve()
        self.session_id = session_id
        self.state_dir = self.project_root / STATE_DIR_NAME
        self.session_file = self.state_dir / SESSION_FILE_NAME
        self.history_file = self.state_dir / HISTORY_FILE_NAME
        self.connection_file = self.state_dir / f"kernel-{self.session_id}.json"

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def load_session(self) -> SessionInfo | None:
        if not self.session_file.exists():
            return None
        payload = json.loads(self.session_file.read_text(encoding="utf-8"))
        session = SessionInfo.from_dict(payload)
        if session.session_id != self.session_id:
            return None
        return session

    def save_session(self, session: SessionInfo) -> None:
        self.ensure_state_dir()
        self.session_file.write_text(
            json.dumps(session.to_dict(), ensure_ascii=True), encoding="utf-8"
        )

    def clear_session(self) -> None:
        if self.session_file.exists():
            self.session_file.unlink()

    def cleanup_stale(self) -> bool:
        session = self.load_session()
        if session is None:
            return False

        connection = Path(session.connection_file)
        stale = not pid_exists(session.pid) or not connection.exists()
        if not stale:
            return False

        if connection.exists():
            connection.unlink()
        self.clear_session()
        return True

    def ensure_gitignore_entry(self) -> bool:
        self.ensure_state_dir()
        gitignore = self.project_root / ".gitignore"
        entry = f"{STATE_DIR_NAME}/"

        if not gitignore.exists():
            gitignore.write_text(f"{entry}\n", encoding="utf-8")
            return True

        lines = gitignore.read_text(encoding="utf-8").splitlines()
        if entry in lines:
            return False

        suffix = "" if gitignore.read_text(encoding="utf-8").endswith("\n") else "\n"
        with gitignore.open("a", encoding="utf-8") as handle:
            handle.write(f"{suffix}{entry}\n")
        return True

    def append_history(self, entry: dict[str, Any]) -> None:
        self.ensure_state_dir()
        with self.history_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True))
            handle.write("\n")

    def read_history(self, errors_only: bool = False) -> list[dict[str, Any]]:
        if not self.history_file.exists():
            return []

        entries: list[dict[str, Any]] = []
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if errors_only and entry.get("status") != "error":
                continue
            entries.append(entry)
        return entries
