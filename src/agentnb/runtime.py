from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from .backend import BackendExecutionTimeout, LocalIPythonBackend, RuntimeBackend
from .contracts import ExecutionResult, KernelStatus
from .errors import (
    ExecutionTimedOutError,
    KernelNotReadyError,
    NoKernelRunningError,
    SessionBusyError,
    SessionNotFoundError,
)
from .history import HistoryStore
from .hooks import Hooks
from .provisioner import KernelProvisioner
from .session import DEFAULT_SESSION_ID, SessionInfo, SessionStore


class KernelRuntime:
    def __init__(
        self,
        backend: RuntimeBackend | None = None,
        hooks: Hooks | None = None,
        provisioner_factory: Callable[[Path], KernelProvisioner] | None = None,
    ) -> None:
        self._backend = backend or LocalIPythonBackend()
        self._hooks = hooks or Hooks()
        self._provisioner_factory = provisioner_factory or (
            lambda project_root: KernelProvisioner(project_root)
        )

    def start(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        python_executable: Path | None = None,
        auto_install: bool = False,
    ) -> tuple[KernelStatus, bool]:
        store = SessionStore(project_root=project_root, session_id=session_id)
        store.cleanup_stale()

        existing = store.load_session()
        if existing is not None:
            existing_status = self._backend.status(existing)
            if existing_status.alive:
                return existing_status, False
            store.clear_session()

        provisioner = self._provisioner_factory(store.project_root)
        provisioned = provisioner.provision(
            preferred_python=python_executable, auto_install=auto_install
        )

        session = self._backend.start(
            project_root=store.project_root,
            state_dir=store.state_dir,
            session_id=session_id,
            python_executable=provisioned.executable,
        )
        store.save_session(session)
        store.ensure_gitignore_entry()
        self._hooks.on_kernel_start(store.project_root, session_id, session)
        status = self._backend.status(session)
        return status, True

    def status(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> KernelStatus:
        store = SessionStore(project_root=project_root, session_id=session_id)
        store.cleanup_stale()
        session = store.load_session()
        if session is None:
            return KernelStatus(alive=False)

        status = self._backend.status(session)
        if not status.alive:
            store.clear_session()
        return status

    def stop(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> None:
        store, session = self._require_session(project_root=project_root, session_id=session_id)
        self._backend.stop(session)
        store.clear_session()
        self._hooks.on_kernel_stop(store.project_root, session_id, session)

    def list_sessions(self, project_root: Path) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for session in SessionStore.list_sessions(project_root):
            store = SessionStore(project_root=project_root, session_id=session.session_id)
            store.cleanup_stale()
            current = store.load_session()
            if current is None:
                continue
            status = self._backend.status(current)
            if not status.alive:
                store.delete_session()
                continue
            entries.append(
                {
                    "session_id": current.session_id,
                    "alive": status.alive,
                    "pid": status.pid,
                    "connection_file": status.connection_file,
                    "started_at": status.started_at,
                    "uptime_s": status.uptime_s,
                    "python": status.python,
                    "last_activity": self._last_activity(project_root, current.session_id),
                    "is_default": current.session_id == DEFAULT_SESSION_ID,
                }
            )
        return entries

    def delete_session(self, project_root: Path, session_id: str) -> dict[str, object]:
        store = SessionStore(project_root=project_root, session_id=session_id)
        store.cleanup_stale()
        session = store.load_session()
        if session is None:
            raise SessionNotFoundError(session_id)

        status = self._backend.status(session)
        stopped = status.alive
        if status.alive:
            self._backend.stop(session)

        store.delete_session()
        return {
            "deleted": True,
            "session_id": session_id,
            "stopped_running_kernel": stopped,
        }

    def execute(
        self,
        project_root: Path,
        code: str,
        timeout_s: float,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> ExecutionResult:
        store, session = self._require_session(project_root=project_root, session_id=session_id)
        self._hooks.before_execute(store.project_root, session_id, code)

        error: Exception | None = None
        result: ExecutionResult | None = None
        try:
            with store.acquire_command_lock() as lock_acquired:
                if not lock_acquired:
                    raise SessionBusyError()
                try:
                    result = self._backend.execute(session=session, code=code, timeout_s=timeout_s)
                except BackendExecutionTimeout as exc:
                    self._backend.interrupt(session)
                    raise ExecutionTimedOutError(timeout_s) from exc

            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            self._hooks.after_execute(store.project_root, session_id, code, result, error)

    def interrupt(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> None:
        _, session = self._require_session(project_root=project_root, session_id=session_id)
        self._backend.interrupt(session)

    def reset(
        self,
        project_root: Path,
        timeout_s: float = 10.0,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> ExecutionResult:
        store, session = self._require_session(project_root=project_root, session_id=session_id)
        with store.acquire_command_lock() as lock_acquired:
            if not lock_acquired:
                raise SessionBusyError()
            return self._backend.reset(session=session, timeout_s=timeout_s)

    def history(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        errors_only: bool = False,
        include_internal: bool = False,
    ) -> list[dict[str, object]]:
        history_store = HistoryStore(project_root=project_root, session_id=session_id)
        return [
            entry.to_dict()
            for entry in history_store.read(
                errors_only=errors_only,
                include_internal=include_internal,
            )
        ]

    def doctor(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        python_executable: Path | None = None,
        auto_fix: bool = False,
    ) -> dict[str, object]:
        store = SessionStore(project_root=project_root, session_id=session_id)
        stale_cleaned = store.cleanup_stale()
        session_exists = store.load_session() is not None
        report = self._provisioner_factory(store.project_root).doctor(
            preferred_python=python_executable,
            auto_fix=auto_fix,
        )
        payload = report.to_dict()
        payload["stale_session_cleaned"] = stale_cleaned
        payload["session_exists"] = session_exists
        return payload

    def _require_session(
        self, project_root: Path, session_id: str
    ) -> tuple[SessionStore, SessionInfo]:
        store = SessionStore(project_root=project_root, session_id=session_id)
        store.cleanup_stale()
        session = store.load_session()
        if session is None:
            if store.has_connection_file():
                raise KernelNotReadyError()
            raise NoKernelRunningError()

        status = self._backend.status(session)
        if not status.alive:
            if store.has_connection_file():
                raise KernelNotReadyError()
            store.clear_session()
            raise NoKernelRunningError()

        return store, session

    def _last_activity(self, project_root: Path, session_id: str) -> str | None:
        history = HistoryStore(project_root=project_root, session_id=session_id).read(
            include_internal=True
        )
        if not history:
            return None
        return history[-1].ts
