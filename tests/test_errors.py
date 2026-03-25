from __future__ import annotations

from agentnb.contracts import HelperAccessMetadata
from agentnb.errors import AgentNBException, ErrorContext


def test_error_context_serializes_helper_access_and_known_fields_without_extra_defaults() -> None:
    context = ErrorContext(
        helper_access=HelperAccessMetadata(
            waited=True,
            waited_for="idle",
            waited_ms=12,
            initial_runtime_state="busy",
        ),
        runtime_state="ready",
        interrupt_recommended=False,
        active_execution_id=None,
        extras={"source_path": "/tmp/example.py"},
        _include_helper_access=True,
        _include_null_fields=frozenset({"active_execution_id"}),
    )

    assert context.to_data() == {
        "waited": True,
        "waited_for": "idle",
        "waited_ms": 12,
        "initial_runtime_state": "busy",
        "runtime_state": "ready",
        "interrupt_recommended": False,
        "active_execution_id": None,
        "source_path": "/tmp/example.py",
    }


def test_error_context_merge_preserves_existing_fields_and_overrides_updates() -> None:
    initial = ErrorContext(
        session_id="default",
        helper_access=HelperAccessMetadata(waited_ms=5),
        _include_helper_access=True,
    )
    updated = initial.merge(
        ErrorContext(
            session_source="remembered",
            helper_access=HelperAccessMetadata(waited=True, waited_ms=7),
            _include_helper_access=True,
        )
    )

    assert updated.to_data() == {
        "waited": True,
        "waited_ms": 12,
        "session_id": "default",
        "session_source": "remembered",
    }


def test_error_context_merge_can_clear_explicitly_null_active_execution_id() -> None:
    initial = ErrorContext(active_execution_id="run-1")
    cleared = initial.merge(ErrorContext.from_data({"active_execution_id": None}))

    assert cleared.active_execution_id is None
    assert cleared.to_data() == {"active_execution_id": None}


def test_error_context_merge_preserves_earliest_initial_runtime_state() -> None:
    initial = ErrorContext(
        helper_access=HelperAccessMetadata(
            waited=False,
            initial_runtime_state="ready",
        )
    )
    updated = initial.merge(
        ErrorContext(
            helper_access=HelperAccessMetadata(
                waited=True,
                waited_ms=7,
                initial_runtime_state="busy",
            ),
            _include_helper_access=True,
        )
    )

    assert updated.to_data() == {
        "waited": True,
        "waited_ms": 7,
        "initial_runtime_state": "ready",
    }


def test_agent_exception_data_is_derived_from_error_context() -> None:
    error = AgentNBException(
        code="INVALID_INPUT",
        message="boom",
        error_context=ErrorContext(input_shape="exec_file_path", source_path="/tmp/test.py"),
    )

    assert error.data == {
        "input_shape": "exec_file_path",
        "source_path": "/tmp/test.py",
    }

    error.data = {"session_id": "analysis"}

    assert error.error_context.session_id == "analysis"
    assert error.data == {"session_id": "analysis"}
