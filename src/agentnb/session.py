from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .errors import InvalidInputError

DEFAULT_SESSION_ID = "default"
STATE_DIR_NAME = ".agentnb"
LEGACY_SESSION_FILE_NAME = "session.json"
COMMAND_LOCK_FILE_NAME = "command.lock"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


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
        session_id = validate_session_id(str(payload["session_id"]))
        return cls(
            session_id=session_id,
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


def validate_session_id(session_id: str) -> str:
    normalized = session_id.strip()
    if SESSION_ID_PATTERN.fullmatch(normalized):
        return normalized
    raise InvalidInputError(
        "Invalid session name. Use 1-64 characters: letters, digits, '.', '_', or '-'."
    )


class SessionStore:
    def __init__(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> None:
        self.project_root = project_root.resolve()
        self.session_id = validate_session_id(session_id)
        self.state_dir = self.project_root / STATE_DIR_NAME
        self.session_file = self.state_dir / _session_file_name(self.session_id)
        self.legacy_session_file = self.state_dir / LEGACY_SESSION_FILE_NAME
        self.connection_file = self.state_dir / f"kernel-{self.session_id}.json"
        self.log_file = self.state_dir / f"kernel-{self.session_id}.log"
        self.command_lock_file = self.state_dir / f"{COMMAND_LOCK_FILE_NAME}-{self.session_id}"

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

    def clear_runtime_files(self) -> None:
        self._safe_unlink(self.connection_file)
        self._safe_unlink(self.log_file)
        self._safe_unlink(self.command_lock_file)

    def delete_session(self) -> None:
        self.clear_runtime_files()
        self.clear_session()

    def has_connection_file(self) -> bool:
        return self.connection_file.exists()

    def has_active_command_lock(self) -> bool:
        if not self.command_lock_file.exists():
            return False
        return not self._clear_stale_command_lock()

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

    @contextmanager
    def acquire_command_lock(self) -> Any:
        self.ensure_state_dir()
        lock_acquired = self._try_create_command_lock()
        if not lock_acquired and self._clear_stale_command_lock():
            lock_acquired = self._try_create_command_lock()
        try:
            yield lock_acquired
        finally:
            if lock_acquired:
                self._safe_unlink(self.command_lock_file)

    @classmethod
    def list_sessions(cls, project_root: Path) -> list[SessionInfo]:
        state_dir = project_root.resolve() / STATE_DIR_NAME
        if not state_dir.exists():
            return []

        default_store = cls(project_root=project_root, session_id=DEFAULT_SESSION_ID)
        if default_store.legacy_session_file.exists():
            default_store.load_session()

        sessions: list[SessionInfo] = []
        for path in sorted(state_dir.glob("session-*.json")):
            session = cls._load_session_file(path)
            if session is None:
                continue
            store = cls(project_root=project_root, session_id=session.session_id)
            sessions.append(store._normalize_session(session))

        return sorted(
            sessions,
            key=lambda session: (session.session_id != DEFAULT_SESSION_ID, session.session_id),
        )

    def _session_paths(self) -> tuple[Path, ...]:
        if self.legacy_session_file == self.session_file:
            return (self.session_file,)
        return (self.session_file, self.legacy_session_file)

    @staticmethod
    def _load_session_file(path: Path) -> SessionInfo | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return SessionInfo.from_dict(payload)
        except (OSError, json.JSONDecodeError, TypeError, ValueError, KeyError):
            SessionStore._safe_unlink(path)
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

    @staticmethod
    def _safe_unlink(path: Path) -> None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def _try_create_command_lock(self) -> bool:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.command_lock_file, flags)
        except FileExistsError:
            return False

        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _clear_stale_command_lock(self) -> bool:
        try:
            raw_pid = self.command_lock_file.read_text(encoding="utf-8").strip()
        except OSError:
            return False

        try:
            lock_pid = int(raw_pid)
        except ValueError:
            self._safe_unlink(self.command_lock_file)
            return True

        if pid_exists(lock_pid):
            return False

        self._safe_unlink(self.command_lock_file)
        return True


def _session_file_name(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"session-{digest}.json"
