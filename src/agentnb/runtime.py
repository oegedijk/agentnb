from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .contracts import ExecutionResult, ExecutionSink, KernelStatus
from .errors import (
    AmbiguousSessionError,
    ExecutionTimedOutError,
    KernelDiedError,
    KernelNotReadyError,
    KernelWaitTimedOutError,
    NoKernelRunningError,
    SessionBusyError,
    SessionNotFoundError,
)
from .hooks import Hooks
from .journal import CommandJournal, JournalEntry, JournalQuery, JournalSelection
from .kernel.backend import (
    BackendCapabilities,
    BackendExecutionTimeout,
    LocalIPythonBackend,
    RuntimeBackend,
)
from .kernel.provisioner import KernelProvisioner
from .payloads import DeleteSessionResult, DoctorPayload, SessionSummary
from .session import DEFAULT_SESSION_ID, SessionInfo, SessionStore, pid_exists
from .state import CommandLockInfo, StateRepository

WaitedFor = Literal["ready", "idle"]
RuntimeStateKind = Literal["missing", "starting", "ready", "busy", "dead", "stale"]
RuntimeStaleReason = Literal["missing_process", "missing_connection_file"]


@dataclass(slots=True, frozen=True)
class KernelWaitResult:
    status: KernelStatus
    waited: bool
    waited_for: WaitedFor | None = None
    runtime_state: RuntimeStateKind | None = None


@dataclass(slots=True, frozen=True)
class RuntimeState:
    kind: RuntimeStateKind
    session_id: str
    kernel_status: KernelStatus
    session: SessionInfo | None = None
    command_lock: CommandLockInfo | None = None
    observed_session_record: bool = False
    has_connection_file: bool = False
    has_command_lock: bool = False
    stale_reason: RuntimeStaleReason | None = None

    @property
    def alive(self) -> bool:
        return self.kernel_status.alive

    @property
    def busy(self) -> bool:
        return bool(self.kernel_status.busy)

    @property
    def usable(self) -> bool:
        return self.kind == "ready"

    @property
    def session_exists(self) -> bool:
        return self.kind in {"ready", "busy", "dead"}

    def to_kernel_status(self) -> KernelStatus:
        return KernelStatus(
            alive=self.kernel_status.alive,
            pid=self.kernel_status.pid,
            connection_file=self.kernel_status.connection_file,
            started_at=self.kernel_status.started_at,
            uptime_s=self.kernel_status.uptime_s,
            python=self.kernel_status.python,
            busy=self.kernel_status.busy,
        )


class KernelRuntime:
    def __init__(
        self,
        backend: RuntimeBackend | None = None,
        hooks: Hooks | None = None,
        provisioner_factory: Callable[[Path], KernelProvisioner] | None = None,
    ) -> None:
        self._backend = backend or LocalIPythonBackend()
        self._hooks = hooks or Hooks()
        self._journal = CommandJournal()
        self._provisioner_factory = provisioner_factory or (
            lambda project_root: KernelProvisioner(project_root)
        )

    @property
    def capabilities(self) -> BackendCapabilities:
        return self._backend.capabilities

    def runtime_state(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> RuntimeState:
        _, state = self._resolve_runtime_state(
            project_root=project_root,
            session_id=session_id,
        )
        return state

    def start(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        python_executable: Path | None = None,
        auto_install: bool = False,
    ) -> tuple[KernelStatus, bool]:
        store, state = self._resolve_runtime_state(
            project_root=project_root,
            session_id=session_id,
        )
        if state.kind in {"ready", "busy"}:
            return state.to_kernel_status(), False
        if state.session is not None:
            store.clear_session()

        provisioner = self._provisioner_factory(store.project_root)
        provisioned = provisioner.provision(
            preferred_python=python_executable, auto_install=auto_install
        )

        session = self._backend.start(
            project_root=store.project_root,
            session_state=store.state,
            python_executable=provisioned.executable,
        )
        store.save_session(session)
        store.ensure_gitignore_entry()
        self._hooks.on_kernel_start(store.project_root, store.session_id, session)
        status = self._backend.status(session)
        return status, True

    def status(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> KernelStatus:
        store, state = self._resolve_runtime_state(
            project_root=project_root,
            session_id=session_id,
        )
        if state.kind == "dead":
            store.clear_session()
        return state.to_kernel_status()

    def stop(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> None:
        store, session = self._require_session(project_root=project_root, session_id=session_id)
        self._backend.stop(session)
        store.clear_session()
        self._hooks.on_kernel_stop(store.project_root, store.session_id, session)

    def stop_starting(self, project_root: Path, session_id: str = DEFAULT_SESSION_ID) -> None:
        store = SessionStore(project_root=project_root, session_id=session_id)
        session = store.load_session()
        if session is None:
            raise NoKernelRunningError()
        self._backend.stop(session)
        store.delete_session()
        self._hooks.on_kernel_stop(store.project_root, store.session_id, session)

    def ensure_started(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> tuple[KernelStatus, bool]:
        return self.start(project_root=project_root, session_id=session_id)

    def list_sessions(self, project_root: Path) -> list[SessionSummary]:
        current_session_id = self.current_session_id(project_root=project_root)
        entries: list[SessionSummary] = []
        for session in SessionStore.list_sessions(project_root):
            store, state = self._resolve_runtime_state(
                project_root=project_root,
                session_id=session.session_id,
            )
            if state.kind not in {"ready", "busy"} or state.session is None:
                store.delete_session()
                continue
            status = state.to_kernel_status()
            current = state.session
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
                    "is_current": current.session_id == current_session_id,
                }
            )
        return entries

    def delete_session(self, project_root: Path, session_id: str) -> DeleteSessionResult:
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
        self.clear_current_session_id(project_root=project_root, expected_session_id=session_id)
        return {
            "deleted": True,
            "session_id": session_id,
            "stopped_running_kernel": stopped,
        }

    def resolve_session_id(
        self,
        project_root: Path,
        requested_session_id: str | None,
        *,
        require_live_session: bool,
    ) -> str:
        if requested_session_id is not None:
            self._check_session_prefix_collision(
                project_root=project_root,
                requested_session_id=requested_session_id,
            )
            return requested_session_id

        preferred_session_id = self.current_session_id(project_root=project_root)
        if not require_live_session:
            return preferred_session_id or DEFAULT_SESSION_ID

        live_session_ids = self._live_session_ids_for_resolution(project_root=project_root)
        if preferred_session_id is not None:
            if not live_session_ids:
                return preferred_session_id
            if preferred_session_id in live_session_ids:
                return preferred_session_id
        if not live_session_ids:
            return DEFAULT_SESSION_ID
        if len(live_session_ids) == 1:
            return live_session_ids[0]
        raise AmbiguousSessionError(list(live_session_ids))

    def _check_session_prefix_collision(
        self, *, project_root: Path, requested_session_id: str
    ) -> None:
        live_ids = self._live_session_ids_for_resolution(project_root=project_root)
        if not live_ids:
            return
        if requested_session_id in live_ids:
            return
        prefix_matches = [sid for sid in live_ids if sid.startswith(requested_session_id)]
        if prefix_matches:
            raise AmbiguousSessionError(prefix_matches)

    def _live_session_ids_for_resolution(self, *, project_root: Path) -> list[str]:
        live_ids: list[str] = []
        for session in SessionStore.list_sessions(project_root):
            if not pid_exists(session.pid):
                continue
            if not Path(session.connection_file).exists():
                continue
            live_ids.append(session.session_id)
        return live_ids

    def current_session_id(self, *, project_root: Path) -> str | None:
        return StateRepository(project_root).session_preferences().current_session_id

    def remember_current_session(self, *, project_root: Path, session_id: str) -> None:
        canonical_session_id = SessionStore(
            project_root=project_root, session_id=session_id
        ).session_id
        StateRepository(project_root).set_current_session_id(canonical_session_id)

    def clear_current_session_id(
        self,
        *,
        project_root: Path,
        expected_session_id: str | None = None,
    ) -> None:
        StateRepository(project_root).clear_current_session_id(
            expected_session_id=expected_session_id
        )

    def wait_for_ready(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> KernelStatus:
        deadline = time.monotonic() + timeout_s
        while True:
            state = self.runtime_state(project_root=project_root, session_id=session_id)
            if state.kind == "dead":
                raise KernelDiedError()
            if state.alive:
                return state.to_kernel_status()
            if time.monotonic() >= deadline:
                raise KernelWaitTimedOutError(timeout_s, waiting_for="ready")
            time.sleep(poll_interval_s)

    def wait_for_idle(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> KernelStatus:
        deadline = time.monotonic() + timeout_s
        while True:
            state = self.runtime_state(project_root=project_root, session_id=session_id)
            if state.kind == "dead":
                raise KernelDiedError()
            status = state.to_kernel_status()
            if status.alive and not status.busy:
                return status
            if time.monotonic() >= deadline:
                raise KernelWaitTimedOutError(timeout_s, waiting_for="idle")
            time.sleep(poll_interval_s)

    def wait_for_usable(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        *,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> KernelWaitResult:
        state = self.runtime_state(project_root=project_root, session_id=session_id)
        status = state.to_kernel_status()
        if state.kind == "dead":
            raise KernelDiedError()
        if state.alive:
            if not state.busy:
                return KernelWaitResult(status=status, waited=False, runtime_state=state.kind)
            return KernelWaitResult(
                status=self.wait_for_idle(
                    project_root=project_root,
                    session_id=session_id,
                    timeout_s=timeout_s,
                    poll_interval_s=poll_interval_s,
                ),
                waited=True,
                waited_for="idle",
                runtime_state="ready",
            )
        return KernelWaitResult(
            status=self.wait_for_ready(
                project_root=project_root,
                session_id=session_id,
                timeout_s=timeout_s,
                poll_interval_s=poll_interval_s,
            ),
            waited=True,
            waited_for="ready",
            runtime_state="ready",
        )

    def execute(
        self,
        project_root: Path,
        code: str,
        timeout_s: float,
        session_id: str = DEFAULT_SESSION_ID,
        *,
        before_backend: Callable[[], None] | None = None,
        event_sink: ExecutionSink | None = None,
    ) -> ExecutionResult:
        store, session = self._require_session(project_root=project_root, session_id=session_id)
        self._hooks.before_execute(store.project_root, store.session_id, code)

        error: Exception | None = None
        result: ExecutionResult | None = None
        exec_started = time.monotonic()
        try:
            with store.acquire_command_lock() as lock_acquired:
                if not lock_acquired:
                    raise self._session_busy_error(store)
                try:
                    if before_backend is not None:
                        before_backend()
                    result = self._backend.execute(
                        session=session,
                        code=code,
                        timeout_s=timeout_s,
                        event_sink=event_sink,
                    )
                except BackendExecutionTimeout as exc:
                    self._backend.interrupt(session)
                    elapsed_ms = int((time.monotonic() - exec_started) * 1000)
                    raise ExecutionTimedOutError(timeout_s, duration_ms=elapsed_ms) from exc

            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            self._hooks.after_execute(store.project_root, store.session_id, code, result, error)

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
                raise self._session_busy_error(store)
            return self._backend.reset(session=session, timeout_s=timeout_s)

    def history(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        errors_only: bool = False,
        include_internal: bool = False,
        latest: bool = False,
        last: int | None = None,
        replayable_only: bool = False,
        execution_id: str | None = None,
    ) -> list[JournalEntry]:
        selection = self.select_history(
            project_root=project_root,
            query=JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                errors_only=errors_only,
                latest=latest,
                last=last,
                replayable_only=replayable_only,
                execution_id=execution_id,
            ),
        )
        return selection.entries

    def select_history(
        self,
        *,
        project_root: Path,
        query: JournalQuery,
    ) -> JournalSelection:
        return self._journal.select(project_root=project_root, query=query)

    def doctor(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        python_executable: Path | None = None,
        auto_fix: bool = False,
    ) -> DoctorPayload:
        store, state = self._resolve_runtime_state(
            project_root=project_root,
            session_id=session_id,
        )
        report = self._provisioner_factory(store.project_root).doctor(
            preferred_python=python_executable,
            auto_fix=auto_fix,
        )
        payload = cast(DoctorPayload, report.to_dict())
        status = state.to_kernel_status()
        payload["stale_session_cleaned"] = state.kind == "stale"
        payload["session_exists"] = state.session_exists
        payload["kernel_alive"] = status.alive
        payload["kernel_pid"] = status.pid

        return payload

    def _require_session(
        self, project_root: Path, session_id: str
    ) -> tuple[SessionStore, SessionInfo]:
        store, state = self._resolve_runtime_state(
            project_root=project_root,
            session_id=session_id,
        )
        if state.kind in {"ready", "busy"} and state.session is not None:
            return store, state.session
        if state.kind == "starting":
            raise KernelNotReadyError()
        if state.kind == "dead":
            raise KernelDiedError()
        raise NoKernelRunningError()

    def _last_activity(self, project_root: Path, session_id: str) -> str | None:
        return self._journal.last_activity(project_root=project_root, session_id=session_id)

    def _resolve_runtime_state(
        self,
        *,
        project_root: Path,
        session_id: str,
    ) -> tuple[SessionStore, RuntimeState]:
        store = SessionStore(project_root=project_root, session_id=session_id)
        session = store.load_session()
        observed_session_record = session is not None
        has_connection_file = store.has_connection_file()

        if session is None:
            if has_connection_file:
                return store, RuntimeState(
                    kind="starting",
                    session_id=store.session_id,
                    kernel_status=KernelStatus(alive=False),
                    observed_session_record=False,
                    has_connection_file=True,
                )
            return store, RuntimeState(
                kind="missing",
                session_id=store.session_id,
                kernel_status=KernelStatus(alive=False),
            )

        if not pid_exists(session.pid) or not Path(session.connection_file).exists():
            stale_reason: RuntimeStaleReason = (
                "missing_process" if not pid_exists(session.pid) else "missing_connection_file"
            )
            store.cleanup_stale()
            return store, RuntimeState(
                kind="stale",
                session_id=session.session_id,
                session=session,
                kernel_status=KernelStatus(alive=False),
                observed_session_record=observed_session_record,
                has_connection_file=False,
                stale_reason=stale_reason,
            )

        backend_status = self._backend.status(session)
        if backend_status.alive:
            command_lock = store.command_lock_info()
            busy = command_lock is not None
            status = KernelStatus(
                alive=True,
                pid=backend_status.pid,
                connection_file=backend_status.connection_file,
                started_at=backend_status.started_at,
                uptime_s=backend_status.uptime_s,
                python=backend_status.python,
                busy=busy,
            )
            return store, RuntimeState(
                kind="busy" if busy else "ready",
                session_id=session.session_id,
                session=session,
                command_lock=command_lock,
                kernel_status=status,
                observed_session_record=observed_session_record,
                has_connection_file=True,
                has_command_lock=busy,
            )

        return store, RuntimeState(
            kind="dead",
            session_id=session.session_id,
            session=session,
            kernel_status=KernelStatus(
                alive=False,
                pid=backend_status.pid,
                connection_file=backend_status.connection_file,
                started_at=backend_status.started_at,
                uptime_s=backend_status.uptime_s,
                python=backend_status.python,
                busy=False,
            ),
            observed_session_record=observed_session_record,
            has_connection_file=True,
        )

    def _session_busy_error(self, store: SessionStore) -> SessionBusyError:
        command_lock = store.command_lock_info()
        return SessionBusyError(
            wait_behavior="immediate",
            waited_ms=0,
            lock_pid=command_lock.pid if command_lock is not None else None,
            lock_acquired_at=command_lock.acquired_at if command_lock is not None else None,
            busy_for_ms=command_lock.busy_for_ms() if command_lock is not None else None,
        )
