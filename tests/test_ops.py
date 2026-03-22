from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path

import pytest

from agentnb.errors import AgentNBException
from agentnb.ops import NotebookOps
from agentnb.runtime import KernelRuntime
from tests.conftest import TestLocalIPythonBackend
from tests.helpers import create_project_dir, reset_integration_kernel

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


@pytest.fixture(scope="module")
def integration_project_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create_project_dir(tmp_path_factory.mktemp("ops-integration"))


@pytest.fixture(scope="module")
def integration_runtime() -> KernelRuntime:
    return KernelRuntime(backend=TestLocalIPythonBackend())


@pytest.fixture(scope="module")
def started_runtime_module(
    integration_runtime: KernelRuntime,
    integration_project_dir: Path,
) -> Iterator[tuple[KernelRuntime, Path]]:
    integration_runtime.start(integration_project_dir)
    try:
        yield integration_runtime, integration_project_dir
    finally:
        with suppress(Exception):
            integration_runtime.stop(integration_project_dir)


@pytest.fixture(autouse=True)
def clean_started_runtime(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> Iterator[None]:
    runtime, project_dir = started_runtime_module
    reset_integration_kernel(runtime, project_dir, clear_project_modules=True)
    yield


def _write_module(project_dir: Path, name: str, body: str) -> None:
    (project_dir / f"{name}.py").write_text(body, encoding="utf-8")


def test_ops_vars_inspect_reload(started_runtime_module: tuple[KernelRuntime, Path]) -> None:
    runtime, project_dir = started_runtime_module
    _write_module(
        project_dir,
        "localmod",
        """
def greet() -> str:
    return "v1"
""".lstrip(),
    )
    runtime.execute(project_root=project_dir, code="my_value = [1, 2, 3]", timeout_s=5)
    runtime.execute(
        project_root=project_dir,
        code="""
from localmod import greet
import localmod
""",
        timeout_s=5,
    )

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
    assert inspect_payload["preview"] == {
        "kind": "sequence-like",
        "length": 3,
        "sample": [1, 2, 3],
        "item_type": "int",
    }
    assert inspect_payload["members"] == []
    assert inspect_payload["doc"] == ""

    _write_module(
        project_dir,
        "localmod",
        """
def greet() -> str:
    return "v2"
""".lstrip(),
    )

    before_reload = runtime.execute(
        project_root=project_dir,
        code="(greet(), localmod.greet())",
        timeout_s=5,
    )
    assert before_reload.result == "('v1', 'v1')"

    reload_payload = ops.reload_module(project_root=project_dir, module_name="localmod")
    assert reload_payload["requested_module"] == "localmod"
    assert reload_payload["reloaded_modules"] == ["localmod"]
    assert "greet" in reload_payload["rebound_names"]
    assert reload_payload["failed_modules"] == []

    after_reload = runtime.execute(
        project_root=project_dir,
        code="(greet(), localmod.greet())",
        timeout_s=5,
    )
    assert after_reload.result == "('v2', 'v2')"

    visible_history = runtime.history(project_root=project_dir)
    assert [entry.command_type for entry in visible_history] == ["vars", "inspect", "reload"]
    assert [entry.label for entry in visible_history] == [
        "vars",
        "inspect my_value",
        "reload localmod",
    ]
    assert all(entry.kind == "user_command" for entry in visible_history)

    internal_history = runtime.history(project_root=project_dir, include_internal=True)
    assert len(internal_history) == 6
    helper_entries = [entry for entry in internal_history if entry.kind == "kernel_execution"]
    assert {entry.command_type for entry in helper_entries} == {"vars", "inspect", "reload"}
    assert any("get_ipython" in str(entry.code) for entry in helper_entries)


def test_ops_inspect_dataframe_like_preview(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime_module
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
    assert inspect_payload["members"] == []
    assert inspect_payload["doc"] == ""


def test_ops_inspect_supports_safe_reference_access(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime_module
    runtime.execute(
        project_root=project_dir,
        timeout_s=5,
        code="""
class Holder:
    def __init__(self):
        self.value = {"items": [{"id": 1}, {"id": 2}]}


holder = Holder()
payload = holder.value
""",
    )

    ops = NotebookOps(runtime)

    attr_payload = ops.inspect_var(project_root=project_dir, name="holder.value")
    subscript_payload = ops.inspect_var(project_root=project_dir, name="payload['items'][0]")

    assert attr_payload["name"] == "holder.value"
    assert attr_payload["preview"] == {
        "kind": "mapping-like",
        "length": 1,
        "keys": ["items"],
        "sample": {"items": [{"id": 1}, {"id": 2}]},
    }
    assert subscript_payload["name"] == "payload['items'][0]"
    assert subscript_payload["preview"] == {
        "kind": "mapping-like",
        "length": 1,
        "keys": ["id"],
        "sample": {"id": 1},
    }


def test_ops_inspect_nested_mapping_preview_preserves_structure(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime_module
    runtime.execute(
        project_root=project_dir,
        timeout_s=5,
        code="""
payload = {
    "items": [
        {"id": 1, "meta": {"tags": ["a", "b"], "owner": {"name": "Ada"}}},
        {"id": 2, "meta": {"tags": ["c"], "owner": {"name": "Linus"}}},
    ],
    "paging": {"next": "/page/2", "count": 2},
}
""",
    )

    inspect_payload = NotebookOps(runtime).inspect_var(project_root=project_dir, name="payload")

    assert inspect_payload["preview"] == {
        "kind": "mapping-like",
        "length": 2,
        "keys": ["items", "paging"],
        "sample": {
            "items": [
                {"id": 1, "meta": {"tags": ["a", "b"], "owner": {"name": "Ada"}}},
                {"id": 2, "meta": {"tags": ["c"], "owner": {"name": "Linus"}}},
            ],
            "paging": {"next": "/page/2", "count": 2},
        },
    }


def test_ops_reload_without_module_reloads_imported_project_modules(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime_module
    _write_module(
        project_dir,
        "alpha_mod",
        """
def value() -> str:
    return "alpha-v1"
""".lstrip(),
    )
    _write_module(
        project_dir,
        "beta_mod",
        """
def value() -> str:
    return "beta-v1"
""".lstrip(),
    )
    runtime.execute(
        project_root=project_dir,
        code="""
import math
from alpha_mod import value as alpha_value
import alpha_mod
import beta_mod
""",
        timeout_s=5,
    )

    _write_module(
        project_dir,
        "alpha_mod",
        """
def value() -> str:
    return "alpha-v2"
""".lstrip(),
    )
    _write_module(
        project_dir,
        "beta_mod",
        """
def value() -> str:
    return "beta-v2"
""".lstrip(),
    )

    reload_payload = NotebookOps(runtime).reload_module(project_root=project_dir)

    assert reload_payload["requested_module"] is None
    assert reload_payload["mode"] == "project"
    assert reload_payload["reloaded_modules"] == ["alpha_mod", "beta_mod"]
    assert "alpha_value" in reload_payload["rebound_names"]
    assert reload_payload["failed_modules"] == []
    assert reload_payload["skipped_modules"] == []
    assert reload_payload["excluded_module_count"] > 0

    result = runtime.execute(
        project_root=project_dir,
        code="(alpha_value(), alpha_mod.value(), beta_mod.value())",
        timeout_s=5,
    )
    assert result.result == "('alpha-v2', 'alpha-v2', 'beta-v2')"


def test_ops_history_records_errors_as_semantic_commands(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime_module
    ops = NotebookOps(runtime)

    with pytest.raises(AgentNBException):
        ops.inspect_var(project_root=project_dir, name="missing_value")

    visible_history = runtime.history(project_root=project_dir, errors_only=True)
    assert len(visible_history) == 1
    assert visible_history[0].label == "inspect missing_value"
    assert visible_history[0].kind == "user_command"
    assert visible_history[0].status == "error"

    internal_history = runtime.history(
        project_root=project_dir,
        errors_only=True,
        include_internal=True,
    )
    assert len(internal_history) == 2
    assert sum(1 for entry in internal_history if entry.kind == "kernel_execution") == 1


def test_ops_list_vars_compacts_dataframe_repr(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime_module
    runtime.execute(
        project_root=project_dir,
        timeout_s=5,
        code="""
class FakeFrame:
    shape = (10, 3)
    columns = ["a", "b", "c"]


frame = FakeFrame()
""",
    )

    vars_payload = NotebookOps(runtime).list_vars(project_root=project_dir)
    frame_entry = next(item for item in vars_payload if item["name"] == "frame")
    assert frame_entry["repr"] == "DataFrame shape=(10, 3) columns=a, b, c"


def test_ops_sqlite_rows_get_structural_previews(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime_module
    db_path = project_dir / "rows.db"
    if db_path.exists():
        db_path.unlink()
    runtime.execute(
        project_root=project_dir,
        timeout_s=5,
        code=f"""
import sqlite3

conn = sqlite3.connect({str(db_path)!r})
conn.execute("create table items (id integer primary key, title text)")
conn.executemany("insert into items (title) values (?)", [("a",), ("b",)])
conn.commit()
conn.row_factory = sqlite3.Row
rows = conn.execute("select * from items order by id").fetchall()
""",
    )

    ops = NotebookOps(runtime)
    vars_payload = ops.list_vars(project_root=project_dir)
    row_entry = next(item for item in vars_payload if item["name"] == "rows")
    assert row_entry["repr"] == "list len=2 item_keys=id, title"

    inspect_payload = ops.inspect_var(project_root=project_dir, name="rows")
    preview = inspect_payload["preview"]
    assert preview["kind"] == "sequence-like"
    assert preview["sample_keys"] == ["id", "title"]
    first = preview["sample"][0]
    assert isinstance(first, dict)
    assert first["title"] == "a"


def test_ops_inspect_sequence_preview(started_runtime_module: tuple[KernelRuntime, Path]) -> None:
    runtime, project_dir = started_runtime_module
    runtime.execute(
        project_root=project_dir,
        timeout_s=5,
        code="""
posts = [
    {"id": 1, "title": "a", "body": "alpha"},
    {"id": 2, "title": "b", "body": "beta"},
    {"id": 3, "title": "c", "body": "gamma"},
]
""",
    )

    inspect_payload = NotebookOps(runtime).inspect_var(project_root=project_dir, name="posts")
    assert inspect_payload["preview"]["kind"] == "sequence-like"
    assert inspect_payload["preview"]["length"] == 3
    assert inspect_payload["preview"]["item_type"] == "dict"
    assert inspect_payload["preview"]["sample_keys"] == ["id", "title", "body"]
    assert len(inspect_payload["preview"]["sample"]) == 3
