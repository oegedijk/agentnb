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
    names = {item["name"] for item in vars_payload}
    assert "In" not in names
    assert "Out" not in names
    assert "get_ipython" not in names
    assert "open" not in names

    inspect_payload = ops.inspect_var(project_root=project_dir, name="my_value")
    assert inspect_payload["name"] == "my_value"
    assert inspect_payload["type"] == "list"
    assert inspect_payload["preview"] is None

    reload_payload = ops.reload_module(project_root=project_dir, module_name="math")
    assert reload_payload["module"] == "math"


def test_ops_inspect_dataframe_like_preview(started_runtime: tuple[KernelRuntime, Path]) -> None:
    runtime, project_dir = started_runtime
    runtime.execute(
        project_root=project_dir,
        timeout_s=5,
        code="""
class _DTypes:
    def __init__(self, mapping):
        self._mapping = mapping

    def astype(self, _type_name):
        return self

    def to_dict(self):
        return self._mapping


class _NullCounts:
    def __init__(self, mapping):
        self._mapping = mapping

    def sum(self):
        return self

    def to_dict(self):
        return self._mapping


class _HeadRows:
    def __init__(self, rows):
        self._rows = rows

    def reset_index(self):
        return self

    def to_dict(self, orient="records"):
        assert orient == "records"
        return self._rows


class DataFrameLike:
    shape = (2, 2)
    columns = ["a", "b"]
    dtypes = _DTypes({"a": "int64", "b": "string"})

    def head(self, n):
        return _HeadRows([{"a": 1, "b": "x"}, {"a": 2, "b": "y"}][:n])

    def isna(self):
        return _NullCounts({"a": 0, "b": 1})

    def __repr__(self):
        return "DataFrameLike(a, b)"


frame = DataFrameLike()
""",
    )

    ops = NotebookOps(runtime)
    inspect_payload = ops.inspect_var(project_root=project_dir, name="frame")

    assert inspect_payload["name"] == "frame"
    assert inspect_payload["preview"] is not None
    assert inspect_payload["preview"]["kind"] == "dataframe-like"
    assert inspect_payload["preview"]["shape"] == [2, 2]
    assert inspect_payload["preview"]["columns"] == ["a", "b"]
    assert inspect_payload["preview"]["dtypes"] == {"a": "int64", "b": "string"}
    assert inspect_payload["preview"]["null_counts"] == {"a": 0, "b": 1}
    assert inspect_payload["preview"]["head"] == [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
