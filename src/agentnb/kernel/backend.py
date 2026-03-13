from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from queue import Empty
from typing import Protocol, cast

import zmq
from jupyter_client import BlockingKernelClient

from ..contracts import (
    ExecutionResult,
    ExecutionSink,
    KernelStatus,
    utc_now_iso,
)
from ..errors import BackendOperationError
from ..execution_events import ExecutionResultAccumulator, dispatch_output_item
from ..execution_output import output_item_from_iopub_message
from ..session import SessionInfo, pid_exists
from ..state import kernel_connection_file, kernel_log_file
from .jupyter_protocol import (
    ExecuteInputMessage,
    ShellReplyMessage,
    message_parent_id,
    message_type,
    parse_iopub_message,
    parse_shell_reply_message,
)

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
        connection_file = kernel_connection_file(state_dir, session_id)
        log_file = kernel_log_file(state_dir, session_id)
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
                if message_parent_id(_message_mapping(msg)) != msg_id:
                    continue
                alive = message_type(_message_mapping(msg)) == "kernel_info_reply"
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

                parsed_message = parse_iopub_message(_message_mapping(msg))
                if parsed_message is None or parsed_message.parent_id != msg_id:
                    continue
                if isinstance(parsed_message, ExecuteInputMessage):
                    accumulator.set_execution_count(parsed_message.execution_count)
                    continue

                item = output_item_from_iopub_message(parsed_message)
                if item is None:
                    continue

                dispatch_output_item(
                    accumulator=accumulator,
                    item=item,
                    sink=event_sink,
                )
                if item.kind == "status" and item.state == "idle":
                    idle_received = True

            shell_reply = self._shell_reply(client=client, msg_id=msg_id)
            if shell_reply is not None:
                accumulator.apply_shell_reply(shell_reply)

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

    def _shell_reply(self, client: BlockingKernelClient, msg_id: str) -> ShellReplyMessage | None:
        deadline = time.monotonic() + 1.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                shell_msg = client.get_shell_msg(timeout=remaining)
            except Empty:
                return None
            parsed_message = parse_shell_reply_message(_message_mapping(shell_msg))
            if parsed_message is not None and parsed_message.parent_id == msg_id:
                return parsed_message


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


def _message_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _hard_kill_signal() -> int:
    return getattr(signal, "SIGKILL", signal.SIGTERM)
