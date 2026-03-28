from __future__ import annotations

import pytest
from pytest_mock import MockerFixture

from agentnb.contracts import ExecutionResult, KernelStatus
from agentnb.errors import AgentNBException, SessionBusyError
from agentnb.history import HistoryStore
from agentnb.introspection import HelperExecutionPolicy, KernelIntrospection
from agentnb.introspection_models import (
    DataframePreviewData,
    MappingPreviewData,
    SequencePreviewData,
)
from agentnb.runtime import KernelRuntime, KernelWaitResult


def test_kernel_introspection_returns_payload_and_records_history(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    execute = mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(
            status="ok",
            stdout='{"name": "value", "type": "int", "repr": "1"}\n',
            duration_ms=5,
        ),
    )

    result = KernelIntrospection(runtime).inspect_var(project_root=project_dir, name="value")

    assert result.payload.name == "value"
    assert result.payload.type_name == "int"
    execute.assert_called_once()
    entries = HistoryStore(project_dir).read(include_internal=True)
    assert len(entries) == 2
    assert [entry.kind for entry in entries] == ["kernel_execution", "user_command"]
    assert all(entry.command_type == "inspect" for entry in entries)
    assert all(entry.status == "ok" for entry in entries)


def test_kernel_introspection_parses_preview_omission_metadata(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(
            status="ok",
            stdout=(
                '{"name": "payload", "type": "dict", "preview": {'
                '"kind": "mapping-like", '
                '"length": 6, '
                '"keys": ["alpha", "beta", "gamma"], '
                '"keys_shown": 3, '
                '"sample": {"alpha": 1, "beta": {"nested": [1, 2, 3]}}, '
                '"sample_items_shown": 2, '
                '"sample_truncated": true'
                "}}\n"
            ),
            duration_ms=5,
        ),
    )

    result = KernelIntrospection(runtime).inspect_var(project_root=project_dir, name="payload")

    preview = result.payload.preview
    assert isinstance(preview, MappingPreviewData)
    assert preview.keys_shown == 3
    assert preview.sample_items_shown == 2
    assert preview.sample_truncated is True


def test_kernel_introspection_parses_dataframe_preview_counts(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(
            status="ok",
            stdout=(
                '{"name": "df", "type": "DataFrame", "preview": {'
                '"kind": "dataframe-like", '
                '"shape": [100, 12], '
                '"columns": ["a", "b", "c"], '
                '"column_count": 12, '
                '"columns_shown": 3, '
                '"dtypes": {"a": "int64", "b": "int64"}, '
                '"dtypes_shown": 2, '
                '"null_counts": {"a": 0, "b": 1}, '
                '"null_count_fields_shown": 2, '
                '"head": [{"a": 1, "b": 2}], '
                '"head_rows_shown": 1'
                "}}\n"
            ),
            duration_ms=5,
        ),
    )

    result = KernelIntrospection(runtime).inspect_var(project_root=project_dir, name="df")

    preview = result.payload.preview
    assert isinstance(preview, DataframePreviewData)
    assert preview.columns_shown == 3
    assert preview.dtypes_shown == 2
    assert preview.null_count_fields_shown == 2
    assert preview.head_rows_shown == 1


def test_kernel_introspection_parses_sequence_preview_counts(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(
            status="ok",
            stdout=(
                '{"name": "items", "type": "list", "preview": {'
                '"kind": "sequence-like", '
                '"length": 8, '
                '"sample": [{"id": 1}, {"id": 2}], '
                '"sample_items_shown": 2, '
                '"sample_truncated": true, '
                '"item_type": "dict", '
                '"sample_keys": ["id"], '
                '"sample_keys_shown": 1'
                "}}\n"
            ),
            duration_ms=5,
        ),
    )

    result = KernelIntrospection(runtime).inspect_var(project_root=project_dir, name="items")

    preview = result.payload.preview
    assert isinstance(preview, SequencePreviewData)
    assert preview.sample_items_shown == 2
    assert preview.sample_truncated is True
    assert preview.sample_keys_shown == 1


@pytest.mark.parametrize(
    ("stdout", "expected_message", "expected_error_type"),
    [
        ("", "No output while attempting to inspect variable", "PARSE_ERROR"),
        (
            "not-json\n",
            "Unable to parse JSON payload while attempting to inspect variable",
            "JSONDecodeError",
        ),
    ],
)
def test_kernel_introspection_parse_failures_record_semantic_errors(
    project_dir,
    mocker: MockerFixture,
    stdout: str,
    expected_message: str,
    expected_error_type: str,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(status="ok", stdout=stdout, duration_ms=5),
    )

    with pytest.raises(AgentNBException, match=expected_message):
        KernelIntrospection(runtime).inspect_var(project_root=project_dir, name="value")

    entries = HistoryStore(project_dir).read(include_internal=True)
    assert len(entries) == 2
    assert entries[0].kind == "kernel_execution"
    assert entries[0].status == "ok"
    assert entries[1].kind == "user_command"
    assert entries[1].status == "error"
    assert entries[1].error_type == expected_error_type


def test_kernel_introspection_can_wait_for_helper_access_when_requested(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    wait_for_usable = mocker.patch.object(
        runtime,
        "wait_for_usable",
        return_value=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
            waited_ms=20,
            initial_runtime_state="busy",
        ),
    )
    execute = mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(
            status="ok",
            stdout='{"name": "value", "type": "int", "repr": "1"}\n',
            duration_ms=5,
        ),
    )

    result = KernelIntrospection(runtime).inspect_var(
        project_root=project_dir,
        name="value",
        execution_policy=HelperExecutionPolicy(wait_for_usable=True),
    )

    assert result.payload.name == "value"
    assert result.access_metadata.waited is True
    assert result.access_metadata.waited_for == "idle"
    assert result.access_metadata.waited_ms == 20
    wait_for_usable.assert_called_once()
    execute.assert_called_once()


def test_kernel_introspection_preserves_helper_access_metadata_on_error(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    mocker.patch.object(runtime, "runtime_state", return_value=mocker.Mock(kind="missing"))
    mocker.patch.object(runtime, "ensure_started", return_value=(KernelStatus(alive=True), True))
    mocker.patch.object(
        runtime,
        "wait_for_usable",
        return_value=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
            waited_ms=15,
            initial_runtime_state="busy",
        ),
    )
    mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(
            status="error",
            ename="NameError",
            evalue="Variable 'value' is not defined",
            traceback=["NameError"],
            duration_ms=5,
        ),
    )

    with pytest.raises(AgentNBException) as exc_info:
        KernelIntrospection(runtime).inspect_var(
            project_root=project_dir,
            name="value",
            execution_policy=HelperExecutionPolicy(ensure_started=True, wait_for_usable=True),
        )

    assert exc_info.value.data["started_new_session"] is True
    assert exc_info.value.data["waited"] is True
    assert exc_info.value.data["waited_for"] == "idle"
    assert exc_info.value.data["waited_ms"] == 15
    assert exc_info.value.data["initial_runtime_state"] == "busy"


def test_kernel_introspection_preserves_helper_access_metadata_on_direct_busy_error(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    mocker.patch.object(
        runtime,
        "wait_for_usable",
        return_value=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
            waited_ms=12,
            initial_runtime_state="busy",
        ),
    )
    mocker.patch.object(
        runtime,
        "execute",
        side_effect=SessionBusyError(
            wait_behavior="immediate",
            waited_ms=0,
            lock_pid=456,
            active_execution_id="run-123",
        ),
    )

    with pytest.raises(SessionBusyError) as exc_info:
        KernelIntrospection(runtime).inspect_var(
            project_root=project_dir,
            name="value",
            execution_policy=HelperExecutionPolicy(wait_for_usable=True),
        )

    assert exc_info.value.data["wait_behavior"] == "after_wait"
    assert exc_info.value.data["waited"] is True
    assert exc_info.value.data["waited_for"] == "idle"
    assert exc_info.value.data["waited_ms"] == 12
    assert exc_info.value.data["initial_runtime_state"] == "busy"
    assert exc_info.value.data["active_execution_id"] == "run-123"


def test_kernel_introspection_does_not_probe_live_session_during_helper_autostart(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=mocker.Mock(kind="busy"),
    )
    ensure_started = mocker.patch.object(runtime, "ensure_started")
    mocker.patch.object(
        runtime,
        "wait_for_usable",
        return_value=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
            waited_ms=10,
            initial_runtime_state="busy",
        ),
    )
    mocker.patch.object(
        runtime,
        "execute",
        return_value=ExecutionResult(
            status="ok",
            stdout='{"name": "value", "type": "int", "repr": "1"}\n',
            duration_ms=5,
        ),
    )

    KernelIntrospection(runtime).inspect_var(
        project_root=project_dir,
        name="value",
        execution_policy=HelperExecutionPolicy(ensure_started=True, wait_for_usable=True),
    )

    ensure_started.assert_not_called()


def test_kernel_introspection_rejects_unsafe_reference_syntax(
    project_dir,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime(backend=mocker.Mock())
    execute = mocker.patch.object(runtime, "execute")

    with pytest.raises(
        AgentNBException,
        match=r"Inspect only supports names, dotted access, and constant subscripts\.",
    ):
        KernelIntrospection(runtime).inspect_var(project_root=project_dir, name="value()")

    execute.assert_not_called()
