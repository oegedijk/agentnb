from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_SESSION_ID = "default"
STATE_DIR_NAME = ".agentnb"
LEGACY_SESSION_FILE_NAME = "session.json"


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
        self.session_file = self.state_dir / _session_file_name(session_id)
        self.legacy_session_file = self.state_dir / LEGACY_SESSION_FILE_NAME
        self.connection_file = self.state_dir / f"kernel-{self.session_id}.json"

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def load_session(self) -> SessionInfo | None:
        for path in self._session_paths():
            session = self._load_session_file(path)
            if session is None:
                continue
            if session.session_id != self.session_id:
                continue
            session = self._normalize_session(session)
            if path == self.legacy_session_file:
                self.save_session(session)
                self._safe_unlink(self.legacy_session_file)
            return session
        return None

    def save_session(self, session: SessionInfo) -> None:
        self.ensure_state_dir()
        self.session_file.write_text(
            json.dumps(session.to_dict(), ensure_ascii=True), encoding="utf-8"
        )

    def clear_session(self) -> None:
        self._safe_unlink(self.session_file)

    def has_connection_file(self) -> bool:
        return self.connection_file.exists()

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

    def _session_paths(self) -> tuple[Path, ...]:
        if self.legacy_session_file == self.session_file:
            return (self.session_file,)
        return (self.session_file, self.legacy_session_file)

    def _load_session_file(self, path: Path) -> SessionInfo | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return SessionInfo.from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            self._safe_unlink(path)
            return None

    def _normalize_session(self, session: SessionInfo) -> SessionInfo:
        expected_connection_file = str(self.connection_file)
        if session.connection_file == expected_connection_file:
            return session
        return SessionInfo(
            session_id=session.session_id,
            pid=session.pid,
            connection_file=expected_connection_file,
            python_executable=session.python_executable,
            project_root=session.project_root,
            started_at=session.started_at,
        )

    def _safe_unlink(self, path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _session_file_name(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"session-{digest}.json"
