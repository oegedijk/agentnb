from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentnb.runs.executor import LocalRunExecutor
from agentnb.runs.models import RunPlan
from agentnb.runs.store import ExecutionRecord, ExecutionRun, ExecutionStore
from agentnb.runs.worker import (
    BackgroundWorkerArgumentError,
    BackgroundWorkerRequest,
    main,
    parse_argv,
)


def _background_run(project_dir: Path) -> ExecutionRun:
    return ExecutionRun(
        store=ExecutionStore(project_dir),
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="starting",
            duration_ms=0,
            code="1 + 1",
        ),
    )


def test_local_run_executor_start_background_launches_runs_worker(
    project_dir: Path,
    mocker,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AGENTNB_TEST_ENV", "1")
    expected_env = os.environ.copy()
    popen = mocker.patch("agentnb.runs.executor.subprocess.Popen")
    popen.return_value.pid = 456
    executor = LocalRunExecutor(Mock())

    record = executor.start_background(
        plan=RunPlan.for_exec(
            project_root=project_dir,
            session_id="default",
            code="1 + 1",
            mode="background",
            timeout_s=30.0,
        ),
        run=_background_run(project_dir),
    )

    assert record.status == "running"
    assert record.worker_pid == 456
    popen.assert_called_once()
    args, kwargs = popen.call_args
    assert args == (
        [
            sys.executable,
            "-m",
            "agentnb.runs.worker",
            "--project-root",
            str(project_dir.resolve()),
            "--execution-id",
            "run-1",
        ],
    )
    assert kwargs["cwd"] == str(project_dir.resolve())
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["start_new_session"] is True
    assert kwargs["env"] == expected_env
    assert kwargs["env"] is not os.environ


def test_background_worker_parse_argv_returns_typed_request(project_dir: Path) -> None:
    request = parse_argv(
        [
            "--project-root",
            str(project_dir / "."),
            "--execution-id",
            "run-1",
        ]
    )

    assert request == BackgroundWorkerRequest(
        project_root=project_dir.resolve(),
        execution_id="run-1",
    )


def test_background_worker_parse_argv_raises_typed_error(project_dir: Path) -> None:
    with pytest.raises(
        BackgroundWorkerArgumentError,
        match=r"Missing required argument: --execution-id\.",
    ):
        parse_argv(["--project-root", str(project_dir)])


def test_background_worker_main_delegates_to_local_run_manager(
    project_dir: Path,
    monkeypatch,
) -> None:
    runtime = object()
    calls: dict[str, object] = {}

    class FakeLocalRunManager:
        def __init__(self, runtime_arg: object) -> None:
            calls["runtime"] = runtime_arg

        def complete_background_run(self, *, project_root: Path, execution_id: str) -> None:
            calls["project_root"] = project_root
            calls["execution_id"] = execution_id

    monkeypatch.setattr("agentnb.runtime.KernelRuntime", lambda: runtime)
    monkeypatch.setattr("agentnb.runs.local_manager.LocalRunManager", FakeLocalRunManager)

    result = main(
        [
            "--project-root",
            str(project_dir),
            "--execution-id",
            "run-1",
        ]
    )

    assert result == 0
    assert calls == {
        "runtime": runtime,
        "project_root": project_dir.resolve(),
        "execution_id": "run-1",
    }


def test_background_worker_main_records_parse_failure_for_active_run(project_dir: Path) -> None:
    store = ExecutionStore(project_dir)
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="starting",
            duration_ms=0,
            code="1 + 1",
        )
    )

    result = main(
        [
            "--project-root",
            str(project_dir),
            "--execution-id",
            "run-1",
            "--unexpected",
        ]
    )

    stored = store.get("run-1")
    assert result == 1
    assert stored is not None
    assert stored.status == "error"
    assert stored.ename == "BackgroundWorkerArgumentError"
    assert stored.evalue == "Unexpected argument: --unexpected"
