from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentnb.contracts import ExecutionResult
from agentnb.errors import AgentNBException, NoKernelRunningError
from agentnb.history import HistoryStore
from agentnb.ops import NotebookOps
from agentnb.runtime import KernelRuntime


def test_ops_run_rejects_unknown_operation() -> None:
    ops = NotebookOps(KernelRuntime(backend=Mock()))

    with pytest.raises(AgentNBException, match="Unknown operation: mystery"):
        ops.run("mystery")


def test_ops_run_dispatches_known_operation(project_dir) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.execute = Mock(  # type: ignore[method-assign]
        return_value=ExecutionResult(status="ok", stdout="[]\n", duration_ms=5)
    )

    payload = NotebookOps(runtime).run("vars", project_root=project_dir)

    assert payload == []


def test_ops_list_vars_records_runtime_exception(project_dir) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.execute = Mock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="boom"):
        NotebookOps(runtime).list_vars(project_root=project_dir)

    entries = HistoryStore(project_dir).read(include_internal=True, errors_only=True)
    assert len(entries) == 2
    assert [entry.kind for entry in entries] == ["kernel_execution", "user_command"]
    assert all(entry.command_type == "vars" for entry in entries)


def test_ops_list_vars_reraises_missing_kernel_without_history(project_dir) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.execute = Mock(side_effect=NoKernelRunningError())  # type: ignore[method-assign]

    with pytest.raises(NoKernelRunningError):
        NotebookOps(runtime).list_vars(project_root=project_dir)

    assert HistoryStore(project_dir).read(include_internal=True) == []


def test_ops_list_vars_raises_execution_error_when_helper_fails(project_dir) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.execute = Mock(  # type: ignore[method-assign]
        return_value=ExecutionResult(
            status="error",
            ename="NameError",
            evalue="missing",
            traceback=["tb"],
            duration_ms=5,
        )
    )

    with pytest.raises(AgentNBException, match="Failed to list vars"):
        NotebookOps(runtime).list_vars(project_root=project_dir)

    entries = HistoryStore(project_dir).read(include_internal=True, errors_only=True)
    assert len(entries) == 2
    assert entries[-1].kind == "user_command"
    assert entries[-1].status == "error"


def test_ops_list_vars_raises_parse_error_when_helper_prints_nothing(project_dir) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.execute = Mock(  # type: ignore[method-assign]
        return_value=ExecutionResult(status="ok", stdout="", result=None, duration_ms=5)
    )

    with pytest.raises(AgentNBException, match="No output while attempting to list vars"):
        NotebookOps(runtime).list_vars(project_root=project_dir)

    entries = HistoryStore(project_dir).read(errors_only=True)
    assert len(entries) == 1
    assert entries[0].error_type == "PARSE_ERROR"


def test_ops_list_vars_raises_parse_error_when_helper_prints_invalid_json(project_dir) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.execute = Mock(  # type: ignore[method-assign]
        return_value=ExecutionResult(status="ok", stdout="not-json\n", result=None, duration_ms=5)
    )

    with pytest.raises(
        AgentNBException,
        match="Unable to parse JSON payload while attempting to list vars",
    ):
        NotebookOps(runtime).list_vars(project_root=project_dir)

    entries = HistoryStore(project_dir).read(errors_only=True)
    assert len(entries) == 1
    assert entries[0].error_type == "JSONDecodeError"
