from __future__ import annotations

from pathlib import Path

import pytest

from agentnb.ops import NotebookOps
from agentnb.runtime import KernelRuntime

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


def test_ops_vars_inspect_reload(started_runtime: tuple[KernelRuntime, Path]) -> None:
    runtime, project_dir = started_runtime
    runtime.execute(project_root=project_dir, code="my_value = [1, 2, 3]", timeout_s=5)

    ops = NotebookOps(runtime)
    vars_payload = ops.list_vars(project_root=project_dir)
    assert any(item["name"] == "my_value" for item in vars_payload)

    inspect_payload = ops.inspect_var(project_root=project_dir, name="my_value")
    assert inspect_payload["name"] == "my_value"
    assert inspect_payload["type"] == "list"

    reload_payload = ops.reload_module(project_root=project_dir, module_name="math")
    assert reload_payload["module"] == "math"
