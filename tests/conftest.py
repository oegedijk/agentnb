from __future__ import annotations

import os
import signal
import time
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Literal

import pytest
from click.testing import CliRunner

from agentnb.contracts import KernelStatus
from agentnb.execution import ExecutionRecord, ExecutionService, ExecutionStore
from agentnb.history import HistoryRecord, HistoryStore, user_command_record
from agentnb.introspection import KernelIntrospection
from agentnb.kernel.backend import LocalIPythonBackend, _close_client, _hard_kill_signal
from agentnb.recording import CommandRecorder
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo, pid_exists


class TestLocalIPythonBackend(LocalIPythonBackend):
    """Use aggressive teardown in tests to avoid paying production stop timeouts."""

    __test__ = False

    def stop(self, session: SessionInfo, timeout_s: float = 0.0) -> None:
        del timeout_s
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

        if pid_exists(session.pid):
            os.kill(session.pid, signal.SIGTERM)
            term_deadline = time.monotonic() + 0.05
            while pid_exists(session.pid) and time.monotonic() < term_deadline:
                time.sleep(0.005)

        if pid_exists(session.pid):
            os.kill(session.pid, _hard_kill_signal())
            kill_deadline = time.monotonic() + 0.2
            while pid_exists(session.pid) and time.monotonic() < kill_deadline:
                time.sleep(0.01)

        if connection_file.exists():
            connection_file.unlink()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """
[project]
name = "fixture-project"
version = "0.0.0"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return project


@pytest.fixture
def runtime() -> KernelRuntime:
    return KernelRuntime(backend=TestLocalIPythonBackend())


@pytest.fixture
def patch_cli_runtime(runtime: KernelRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentnb.cli as cli

    executions = ExecutionService(runtime)
    introspection = KernelIntrospection(runtime, session_access=executions)
    monkeypatch.setattr(
        runtime,
        "ensure_started",
        lambda **_: (KernelStatus(alive=True, pid=123), False),
    )
    monkeypatch.setattr(cli, "runtime", runtime)
    monkeypatch.setattr(cli, "introspection", introspection)
    monkeypatch.setattr(cli, "executions", executions)
    monkeypatch.setattr(
        cli,
        "application",
        cli.AgentNBApp(runtime=runtime, executions=executions, introspection=introspection),
    )


@pytest.fixture
def started_runtime(
    runtime: KernelRuntime,
    project_dir: Path,
) -> Iterator[tuple[KernelRuntime, Path]]:
    runtime.start(project_dir)
    try:
        yield runtime, project_dir
    finally:
        with suppress(Exception):
            runtime.stop(project_dir)


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def journal_builder(project_dir: Path):
    def add_history(
        *,
        ts: str,
        session_id: str = "default",
        command_type: str,
        label: str,
        status: str = "ok",
        duration_ms: int = 1,
        input_text: str | None = None,
        error_type: str | None = None,
    ) -> None:
        if command_type in {"vars", "inspect", "history"}:
            classification = "inspection"
        elif command_type in {"reload", "interrupt", "start", "stop"}:
            classification = "control"
        else:
            classification = "replayable"
        HistoryStore(project_dir).append(
            user_command_record(
                ts=ts,
                session_id=session_id,
                classification=classification,
                command_type=command_type,
                label=label,
                input_text=input_text,
                status=status,
                duration_ms=duration_ms,
                error_type=error_type,
            )
        )

    def add_execution(
        *,
        execution_id: str,
        ts: str,
        session_id: str = "default",
        command_type: str = "exec",
        status: Literal["starting", "running", "ok", "error"] = "ok",
        duration_ms: int = 1,
        code: str | None = None,
        result: str | None = None,
        ename: str | None = None,
        failure_origin: Literal["kernel", "control"] | None = None,
        journal_entries: list[HistoryRecord] | None = None,
    ) -> None:
        if journal_entries is None and status in {"ok", "error"}:
            journal_entries = (
                CommandRecorder()
                .for_execution(
                    command_type=command_type,
                    code=code,
                )
                .build_records(
                    ts=ts,
                    session_id=session_id,
                    execution_id=execution_id,
                    status=status,
                    duration_ms=duration_ms,
                    error_type=ename,
                    failure_origin=failure_origin,
                    result=result,
                )
            )
        ExecutionStore(project_dir).append(
            ExecutionRecord(
                execution_id=execution_id,
                ts=ts,
                session_id=session_id,
                command_type=command_type,
                status=status,
                duration_ms=duration_ms,
                code=code,
                result=result,
                ename=ename,
                failure_origin=failure_origin,
                journal_entries=[] if journal_entries is None else journal_entries,
            )
        )

    return {
        "history": add_history,
        "execution": add_execution,
    }
