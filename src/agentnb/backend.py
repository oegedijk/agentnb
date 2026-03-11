from __future__ import annotations

import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import Any, Protocol, cast

import zmq
from jupyter_client import BlockingKernelClient

from .contracts import (
    ExecutionEvent,
    ExecutionResult,
    ExecutionSink,
    KernelStatus,
    utc_now_iso,
)
from .errors import BackendOperationError
from .execution_events import ExecutionResultAccumulator, dispatch_event
from .session import SessionInfo, pid_exists

STARTUP_CODE = """import os
import sys

_project_root = os.environ.get("AGENTNB_PROJECT_ROOT", ".")
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

del _project_root
"""


class BackendExecutionTimeout(TimeoutError):
    pass


class RuntimeBackend(Protocol):
    def start(
        self,
        project_root: Path,
        state_dir: Path,
        session_id: str,
        python_executable: str,
    ) -> SessionInfo: ...

    def status(self, session: SessionInfo, timeout_s: float = 2.0) -> KernelStatus: ...

    def execute(
        self,
        session: SessionInfo,
        code: str,
        timeout_s: float,
        event_sink: ExecutionSink | None = None,
    ) -> ExecutionResult: ...

    def interrupt(self, session: SessionInfo) -> None: ...

    def stop(self, session: SessionInfo, timeout_s: float = 5.0) -> None: ...

    def reset(self, session: SessionInfo, timeout_s: float) -> ExecutionResult: ...


class LocalIPythonBackend:
    def __init__(self, startup_code: str = STARTUP_CODE, startup_timeout_s: float = 10.0) -> None:
        self._startup_code = startup_code
        self._startup_timeout_s = startup_timeout_s
        self._zmq_context = zmq.Context.instance()

    def start(
        self,
        project_root: Path,
        state_dir: Path,
        session_id: str,
        python_executable: str,
    ) -> SessionInfo:
        state_dir.mkdir(parents=True, exist_ok=True)
        connection_file = state_dir / f"kernel-{session_id}.json"
        log_file = state_dir / f"kernel-{session_id}.log"
        if connection_file.exists():
            connection_file.unlink()
        if log_file.exists():
            log_file.unlink()

        env = os.environ.copy()
        env["AGENTNB_PROJECT_ROOT"] = str(project_root)

        with log_file.open("wb") as kernel_log:
            process = subprocess.Popen(
                [python_executable, "-m", "ipykernel_launcher", "-f", str(connection_file)],
                cwd=str(project_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=kernel_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        session = SessionInfo(
            session_id=session_id,
            pid=process.pid,
            connection_file=str(connection_file),
            python_executable=python_executable,
            project_root=str(project_root),
            started_at=utc_now_iso(),
        )

        try:
            self._wait_for_ready(
                session,
                process=process,
                timeout_s=self._startup_timeout_s,
                log_file=log_file,
            )
            startup_result = self.execute(
                session, self._startup_code, timeout_s=self._startup_timeout_s
            )
            if startup_result.status == "error":
                startup_error = startup_result.evalue or startup_result.ename or "unknown error"
                raise BackendOperationError(f"Kernel startup code failed: {startup_error}")
        except Exception:
            self.stop(session, timeout_s=2.0)
            raise

        return session

    def status(self, session: SessionInfo, timeout_s: float = 2.0) -> KernelStatus:
        if not pid_exists(session.pid):
            return KernelStatus(
                alive=False,
                pid=session.pid,
                connection_file=session.connection_file,
                started_at=session.started_at,
                python=session.python_executable,
            )

        connection_file = Path(session.connection_file)
        if not connection_file.exists():
            return KernelStatus(
                alive=False,
                pid=session.pid,
                connection_file=session.connection_file,
                started_at=session.started_at,
                python=session.python_executable,
            )

        alive = False
        client = self._create_client(connection_file)
        try:
            client.start_channels(shell=True, iopub=False, stdin=False, hb=True, control=False)
            msg_id = client.kernel_info()
            deadline = time.monotonic() + timeout_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                msg = client.get_shell_msg(timeout=remaining)
                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue
                alive = msg.get("msg_type") == "kernel_info_reply"
                break
        except Exception:
            alive = bool(client.is_alive())
        else:
            if not alive:
                alive = bool(client.is_alive())
        finally:
            _close_client(client)

        uptime = _uptime_seconds(session.started_at)
        return KernelStatus(
            alive=alive,
            pid=session.pid,
            connection_file=session.connection_file,
            started_at=session.started_at,
            uptime_s=uptime,
            python=session.python_executable,
        )

    def execute(
        self,
        session: SessionInfo,
        code: str,
        timeout_s: float,
        event_sink: ExecutionSink | None = None,
    ) -> ExecutionResult:
        connection_file = Path(session.connection_file)
        if not connection_file.exists():
            raise BackendOperationError("Kernel connection file is missing")

        started = time.monotonic()
        client = self._create_client(connection_file)
        accumulator = ExecutionResultAccumulator()

        try:
            client.start_channels(shell=True, iopub=True, stdin=False, hb=False, control=False)
            msg_id = client.execute(code, allow_stdin=False, stop_on_error=True)
            deadline = started + timeout_s
            idle_received = False

            while not idle_received:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackendExecutionTimeout

                try:
                    msg = client.get_iopub_msg(timeout=remaining)
                except Empty as exc:
                    raise BackendExecutionTimeout from exc

                if msg.get("parent_header", {}).get("msg_id") != msg_id:
                    continue

                msg_type = msg.get("msg_type")
                content = msg.get("content", {})

                if msg_type == "stream":
                    name = content.get("name", "stdout")
                    text = str(content.get("text", ""))
                    dispatch_event(
                        accumulator=accumulator,
                        event=ExecutionEvent(
                            kind=name if name == "stderr" else "stdout",
                            content=text,
                        ),
                        sink=event_sink,
                    )
                elif msg_type == "execute_result":
                    value = _extract_text_plain(content)
                    dispatch_event(
                        accumulator=accumulator,
                        event=ExecutionEvent(kind="result", content=value),
                        sink=event_sink,
                    )
                elif msg_type == "display_data":
                    value = _extract_text_plain(content)
                    if value:
                        dispatch_event(
                            accumulator=accumulator,
                            event=ExecutionEvent(kind="display", content=value),
                            sink=event_sink,
                        )
                elif msg_type == "execute_input":
                    accumulator.set_execution_count(content.get("execution_count"))
                elif msg_type == "error":
                    dispatch_event(
                        accumulator=accumulator,
                        event=ExecutionEvent(
                            kind="error",
                            content=str(content.get("evalue", "")) or None,
                            metadata={
                                "ename": content.get("ename"),
                                "traceback": content.get("traceback") or [],
                            },
                        ),
                        sink=event_sink,
                    )
                elif msg_type == "status":
                    state = content.get("execution_state")
                    if isinstance(state, str):
                        dispatch_event(
                            accumulator=accumulator,
                            event=ExecutionEvent(kind="status", content=state),
                            sink=event_sink,
                        )
                        if state == "idle":
                            idle_received = True

            shell_reply = self._shell_reply(client=client, msg_id=msg_id)
            shell_content: dict[str, object] = {}
            if shell_reply is not None:
                shell_content = _as_dict(shell_reply.get("content"))
            if shell_content:
                accumulator.apply_shell_reply(cast(dict[str, Any], shell_content))

        finally:
            _close_client(client)

        duration_ms = int((time.monotonic() - started) * 1000)
        return accumulator.build(duration_ms=duration_ms)

    def interrupt(self, session: SessionInfo) -> None:
        if not pid_exists(session.pid):
            raise BackendOperationError("Kernel process is not running")
        os.kill(session.pid, signal.SIGINT)

    def stop(self, session: SessionInfo, timeout_s: float = 5.0) -> None:
        connection_file = Path(session.connection_file)
        if connection_file.exists():
            client = self._create_client(connection_file)
            try:
                client.start_channels(shell=False, iopub=False, stdin=False, hb=False, control=True)
                client.shutdown(restart=False)
            except Exception:
                pass
            finally:
                _close_client(client)

        if not pid_exists(session.pid):
            if connection_file.exists():
                connection_file.unlink()
            return

        deadline = time.monotonic() + timeout_s
        while pid_exists(session.pid) and time.monotonic() < deadline:
            time.sleep(0.1)

        if pid_exists(session.pid):
            os.kill(session.pid, signal.SIGTERM)
            term_deadline = time.monotonic() + 2.0
            while pid_exists(session.pid) and time.monotonic() < term_deadline:
                time.sleep(0.1)

        if pid_exists(session.pid):
            os.kill(session.pid, _hard_kill_signal())

        if connection_file.exists():
            connection_file.unlink()

    def reset(self, session: SessionInfo, timeout_s: float) -> ExecutionResult:
        reset_code = """
from IPython import get_ipython
_ip = get_ipython()
if _ip is None:
    raise RuntimeError("No active IPython shell")
_ip.run_line_magic("reset", "-f")
"""
        result = self.execute(session, reset_code, timeout_s=timeout_s)
        if result.status == "ok":
            self.execute(session, self._startup_code, timeout_s=timeout_s)
        return result

    def _create_client(self, connection_file: Path) -> BlockingKernelClient:
        client = BlockingKernelClient(
            connection_file=str(connection_file), context=self._zmq_context
        )
        client.load_connection_file()
        return client

    def _wait_for_ready(
        self,
        session: SessionInfo,
        process: subprocess.Popen[bytes],
        timeout_s: float,
        log_file: Path | None = None,
    ) -> None:
        connection_file = Path(session.connection_file)
        deadline = time.monotonic() + timeout_s
        while not connection_file.exists():
            return_code = process.poll()
            if return_code is not None:
                details = _tail_log(log_file) if log_file is not None else ""
                message = (
                    f"Kernel process exited with code {return_code} before writing connection file"
                )
                if details:
                    message = f"{message}: {details}"
                raise BackendOperationError(message)
            if time.monotonic() >= deadline:
                raise BackendOperationError("Timed out waiting for kernel connection file")
            time.sleep(0.05)

        client = self._create_client(connection_file)
        try:
            client.start_channels(shell=True, iopub=False, stdin=False, hb=False, control=False)
            remaining = max(deadline - time.monotonic(), 0.1)
            msg_id = client.kernel_info()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackendOperationError("Timed out waiting for kernel readiness")
                msg = client.get_shell_msg(timeout=remaining)
                if msg.get("parent_header", {}).get("msg_id") == msg_id:
                    break
        finally:
            _close_client(client)

    def _shell_reply(self, client: BlockingKernelClient, msg_id: str) -> dict[str, object] | None:
        deadline = time.monotonic() + 1.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                shell_msg = client.get_shell_msg(timeout=remaining)
            except Empty:
                return None
            if shell_msg.get("parent_header", {}).get("msg_id") == msg_id:
                return shell_msg


def _extract_text_plain(content: dict[str, object]) -> str | None:
    data = content.get("data")
    if not isinstance(data, dict):
        return None
    data_dict = cast(dict[str, object], data)
    text_plain = data_dict.get("text/plain")
    if text_plain is None:
        return None
    return str(text_plain)


def _uptime_seconds(started_at: str) -> float | None:
    try:
        start = datetime.fromisoformat(started_at)
        return max(time.time() - start.timestamp(), 0.0)
    except ValueError:
        return None


def _tail_log(path: Path, max_chars: int = 400) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def _close_client(client: BlockingKernelClient) -> None:
    try:
        client.stop_channels()
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _as_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _hard_kill_signal() -> int:
    return getattr(signal, "SIGKILL", signal.SIGTERM)
