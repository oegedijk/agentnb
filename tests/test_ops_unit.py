from __future__ import annotations

from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from agentnb.errors import AgentNBException
from agentnb.introspection import KernelIntrospection
from agentnb.ops import NotebookOps
from agentnb.runtime import KernelRuntime


def test_ops_run_rejects_unknown_operation(mocker: MockerFixture) -> None:
    ops = NotebookOps(KernelRuntime(backend=mocker.Mock()))

    with pytest.raises(AgentNBException, match="Unknown operation: mystery"):
        ops.run("mystery")


@pytest.mark.parametrize(
    ("method_name", "call_kwargs", "expected_result"),
    [
        ("list_vars", {"project_root": Path("/tmp/project")}, [{"name": "value"}]),
        (
            "inspect_var",
            {"project_root": Path("/tmp/project"), "name": "value"},
            {"name": "value", "type": "int"},
        ),
        (
            "reload_module",
            {"project_root": Path("/tmp/project"), "module_name": "localmod"},
            {"reloaded_modules": ["localmod"]},
        ),
    ],
)
def test_ops_delegates_to_introspection(
    mocker: MockerFixture,
    method_name: str,
    call_kwargs: dict[str, object],
    expected_result: object,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    introspection = mocker.Mock(spec=KernelIntrospection)
    getattr(introspection, method_name).return_value = expected_result
    ops = NotebookOps(runtime, introspection=introspection)

    result = getattr(ops, method_name)(**call_kwargs)

    assert result == expected_result
    getattr(introspection, method_name).assert_called_once_with(
        **call_kwargs,
        session_id="default",
        timeout_s=10.0,
    )


def test_ops_run_dispatches_known_operation(project_dir, mocker: MockerFixture) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    introspection = mocker.Mock(spec=KernelIntrospection)
    introspection.list_vars.return_value = []
    ops = NotebookOps(runtime, introspection=introspection)

    payload = ops.run("vars", project_root=project_dir)

    assert payload == []
    introspection.list_vars.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=10.0,
    )
