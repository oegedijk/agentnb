from __future__ import annotations

from agentnb.contracts import ExecutionEvent, ExecutionResult
from agentnb.execution_models import ExecutionOutcome, ExecutionTranscript
from agentnb.execution_output import ExecutionOutput, OutputItem


def test_execution_transcript_parity_across_output_event_and_legacy_inputs() -> None:
    output = ExecutionOutput(
        items=[
            OutputItem.stdout("hello\n"),
            OutputItem.result(
                text="[{'id': 1}, {'id': 2}]",
                mime={"text/plain": "[{'id': 1}, {'id': 2}]"},
            ),
        ],
        execution_count=3,
    )
    from_output = ExecutionTranscript.from_output(output)
    from_events = ExecutionTranscript.from_events(
        [
            ExecutionEvent(kind="stdout", content="hello\n"),
            ExecutionEvent(kind="result", content="[{'id': 1}, {'id': 2}]"),
        ],
        execution_count=3,
    )
    from_legacy = ExecutionTranscript.from_legacy_fields(
        stdout="hello\n",
        result="[{'id': 1}, {'id': 2}]",
        status="ok",
        execution_count=3,
    )

    assert from_output.stdout == from_events.stdout == from_legacy.stdout == "hello\n"
    assert (
        from_output.result == from_events.result == from_legacy.result == "[{'id': 1}, {'id': 2}]"
    )
    assert from_output.status == from_events.status == from_legacy.status == "ok"
    assert (
        from_output.result_preview
        == from_events.result_preview
        == from_legacy.result_preview
        == {
            "kind": "sequence-like",
            "length": 2,
            "item_type": "dict",
            "sample_keys": ["id"],
            "sample": [{"id": 1}, {"id": 2}],
        }
    )


def test_execution_outcome_from_execution_result_preserves_normalized_fields() -> None:
    result = ExecutionResult(
        status="ok",
        duration_ms=7,
        outputs=[
            OutputItem.stdout("hello\n"),
            OutputItem.result(text="2", mime={"text/plain": "2"}),
        ],
    )

    outcome = result.to_outcome()

    assert outcome.status == "ok"
    assert outcome.duration_ms == 7
    assert outcome.stdout == "hello\n"
    assert outcome.result == "2"
    assert outcome.events == [
        ExecutionEvent(kind="stdout", content="hello\n"),
        ExecutionEvent(kind="result", content="2", metadata={"mime": {"text/plain": "2"}}),
    ]


def test_execution_outcome_from_exception_preserves_failure_origin_and_error_data() -> None:
    from agentnb.errors import SessionBusyError

    error = SessionBusyError(wait_behavior="immediate", waited_ms=0, lock_pid=123)

    outcome = ExecutionOutcome.from_exception(error, duration_ms=5)

    assert outcome.status == "error"
    assert outcome.failure_origin == "control"
    assert outcome.ename == "SessionBusyError"
    assert outcome.error_data == {
        "wait_behavior": "immediate",
        "waited_ms": 0,
        "lock_pid": 123,
    }
