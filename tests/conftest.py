from __future__ import annotations

import os
import signal
import time
from contextlib import suppress
from pathlib import Path

import pytest
from click.testing import CliRunner

from agentnb.backend import LocalIPythonBackend, _close_client, _hard_kill_signal
from agentnb.ops import NotebookOps
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


@pytest.fixture(autouse=True)
def patch_cli_runtime(runtime: KernelRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    import agentnb.cli as cli

    monkeypatch.setattr(cli, "runtime", runtime)
    monkeypatch.setattr(cli, "ops", NotebookOps(runtime))


@pytest.fixture
def started_runtime(runtime: KernelRuntime, project_dir: Path) -> tuple[KernelRuntime, Path]:
    runtime.start(project_dir)
    try:
        yield runtime, project_dir
    finally:
        with suppress(Exception):
            runtime.stop(project_dir)


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()
