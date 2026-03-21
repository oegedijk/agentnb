from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest
from pytest_mock import MockerFixture

from agentnb.contracts import ExecutionResult
from agentnb.errors import AgentNBException
from agentnb.introspection import HelperExecutionPolicy, KernelHelperResult, KernelIntrospection
from agentnb.ops import NotebookOps
from agentnb.payloads import VarEntry
from agentnb.runtime import KernelRuntime


def test_ops_run_rejects_unknown_operation(mocker: MockerFixture) -> None:
    ops = NotebookOps(KernelRuntime(backend=mocker.Mock()))

    with pytest.raises(AgentNBException, match="Unknown operation: mystery"):
        ops.run("mystery")


@pytest.mark.parametrize(
    ("method_name", "result_method_name", "call_kwargs", "expected_result"),
    [
        (
            "list_vars",
            "list_vars_result",
            {"project_root": Path("/tmp/project")},
            [{"name": "value"}],
        ),
        (
            "inspect_var",
            "inspect_var_result",
            {"project_root": Path("/tmp/project"), "name": "value"},
            {"name": "value", "type": "int"},
        ),
        (
            "reload_module",
            "reload_module_result",
            {"project_root": Path("/tmp/project"), "module_name": "localmod"},
            {"reloaded_modules": ["localmod"]},
        ),
    ],
)
def test_ops_delegates_to_introspection(
    mocker: MockerFixture,
    method_name: str,
    result_method_name: str,
    call_kwargs: dict[str, object],
    expected_result: object,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    introspection = mocker.Mock(spec=KernelIntrospection)
    getattr(introspection, method_name).return_value = KernelHelperResult(
        execution=ExecutionResult(status="ok"),
        payload=expected_result,
    )
    ops = NotebookOps(runtime, introspection=introspection)

    result = getattr(ops, method_name)(**call_kwargs)

    assert result == expected_result
    getattr(introspection, method_name).assert_called_once_with(
        **call_kwargs,
        session_id="default",
        timeout_s=10.0,
        execution_policy=None,
    )
    getattr(introspection, method_name).reset_mock()

    result_payload = getattr(ops, result_method_name)(**call_kwargs)

    assert result_payload == KernelHelperResult(
        execution=ExecutionResult(status="ok"),
        payload=expected_result,
    )
    getattr(introspection, method_name).assert_called_once_with(
        **call_kwargs,
        session_id="default",
        timeout_s=10.0,
        execution_policy=None,
    )


def test_ops_run_dispatches_known_operation(project_dir, mocker: MockerFixture) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    introspection = mocker.Mock(spec=KernelIntrospection)
    introspection.list_vars.return_value = KernelHelperResult(
        execution=ExecutionResult(status="ok"),
        payload=[],
    )
    ops = NotebookOps(runtime, introspection=introspection)

    payload = ops.run("vars", project_root=project_dir)

    assert payload == []
    introspection.list_vars.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=10.0,
        execution_policy=None,
    )


def test_ops_result_methods_preserve_overridden_payload_methods(project_dir) -> None:
    class CustomOps(NotebookOps):
        def list_vars(
            self,
            project_root: Path,
            session_id: str = "default",
            timeout_s: float = 10.0,
            execution_policy: HelperExecutionPolicy | None = None,
        ) -> list[VarEntry]:
            del project_root, session_id, timeout_s, execution_policy
            return [{"name": "value", "type": "int", "repr": "1"}]

    ops = CustomOps(KernelRuntime(backend=Mock()))

    result = ops.list_vars_result(project_root=project_dir)

    assert result.payload == [{"name": "value", "type": "int", "repr": "1"}]
    assert result.execution.status == "ok"
