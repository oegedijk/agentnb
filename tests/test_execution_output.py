from __future__ import annotations

import pytest

from agentnb.contracts import ExecutionEvent
from agentnb.execution_events import ExecutionResultAccumulator, dispatch_output_item
from agentnb.execution_output import (
    output_item_from_iopub_message,
    output_item_from_jupyter_message,
    output_item_from_shell_reply,
    output_item_from_shell_reply_message,
)
from agentnb.jupyter_protocol import parse_iopub_message, parse_shell_reply_message


def test_jupyter_execute_result_preserves_mime_in_legacy_event() -> None:
    item = output_item_from_jupyter_message(
        "execute_result",
        {
            "data": {
                "text/plain": "2",
                "text/html": "<b>2</b>",
            }
        },
    )

    assert item is not None
    assert item.to_event() == ExecutionEvent(
        kind="result",
        content="2",
        metadata={"mime": {"text/plain": "2", "text/html": "<b>2</b>"}},
    )


@pytest.mark.parametrize(
    ("msg_type", "content", "expected_event"),
    [
        (
            "stream",
            {"name": "stdout", "text": "hello\n"},
            ExecutionEvent(kind="stdout", content="hello\n"),
        ),
        (
            "stream",
            {"name": "stderr", "text": "warn\n"},
            ExecutionEvent(kind="stderr", content="warn\n"),
        ),
        (
            "display_data",
            {"data": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
            ExecutionEvent(
                kind="display",
                content="preview",
                metadata={"mime": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
            ),
        ),
        (
            "error",
            {"ename": "ValueError", "evalue": "boom", "traceback": ["tb"]},
            ExecutionEvent(
                kind="error",
                content="boom",
                metadata={"ename": "ValueError", "traceback": ["tb"]},
            ),
        ),
        ("status", {"execution_state": "idle"}, ExecutionEvent(kind="status", content="idle")),
        ("comm_msg", {}, None),
    ],
)
def test_jupyter_message_parser_projects_supported_message_types_to_legacy_events(
    msg_type: str,
    content: dict[str, object],
    expected_event: ExecutionEvent | None,
) -> None:
    item = output_item_from_jupyter_message(msg_type, content)

    if expected_event is None:
        assert item is None
        return

    assert item is not None
    assert item.to_event() == expected_event


def test_jupyter_execute_result_falls_back_when_text_plain_is_missing() -> None:
    item = output_item_from_jupyter_message(
        "execute_result",
        {
            "data": {
                "text/html": "<b>2</b>",
                "application/json": '{"value": 2}',
            }
        },
    )

    assert item is not None
    assert item.to_event() == ExecutionEvent(
        kind="result",
        content='{"value": 2}',
        metadata={"mime": {"text/html": "<b>2</b>", "application/json": '{"value": 2}'}},
    )


def test_accumulator_build_projects_legacy_text_without_losing_display_order() -> None:
    accumulator = ExecutionResultAccumulator()
    for msg_type, content in [
        ("stream", {"name": "stdout", "text": "hello\n"}),
        ("execute_result", {"data": {"text/plain": "2"}}),
        ("display_data", {"data": {"text/plain": "table preview"}}),
    ]:
        item = output_item_from_jupyter_message(msg_type, content)
        assert item is not None
        accumulator.accept_output(item)

    result = accumulator.build(duration_ms=6)

    assert result.stdout == "hello\n"
    assert result.result == "2\ntable preview"
    assert [event.kind for event in result.events] == ["stdout", "result", "display"]
    assert result.events[1].metadata == {"mime": {"text/plain": "2"}}


def test_dispatch_output_item_forwards_projected_legacy_event_to_sink_and_result() -> None:
    accumulator = ExecutionResultAccumulator()

    class Sink:
        def __init__(self) -> None:
            self.events: list[ExecutionEvent] = []

        def started(self, *, execution_id: str, session_id: str) -> None:
            del execution_id, session_id

        def accept(self, event: ExecutionEvent) -> None:
            self.events.append(event)

    sink = Sink()
    item = output_item_from_jupyter_message(
        "display_data",
        {"data": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
    )
    assert item is not None

    dispatch_output_item(accumulator=accumulator, item=item, sink=sink)

    result = accumulator.build(duration_ms=4)
    assert result.result == "preview"
    assert result.events == [
        ExecutionEvent(
            kind="display",
            content="preview",
            metadata={"mime": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
        )
    ]
    assert sink.events == [
        ExecutionEvent(
            kind="display",
            content="preview",
            metadata={"mime": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
        )
    ]


def test_accumulator_shell_reply_refines_error_without_adding_duplicate_event() -> None:
    accumulator = ExecutionResultAccumulator()
    accumulator.accept(
        ExecutionEvent(
            kind="error",
            content="old",
            metadata={"ename": "RuntimeError", "traceback": ["old tb"]},
        )
    )
    shell_reply = parse_shell_reply_message(
        {
            "parent_header": {"msg_id": "run-1"},
            "content": {
                "status": "error",
                "ename": "ValueError",
                "evalue": "new",
                "traceback": ["new tb"],
            },
        }
    )
    assert shell_reply is not None
    accumulator.apply_shell_reply(shell_reply)

    result = accumulator.build(duration_ms=7)

    assert result.status == "error"
    assert result.ename == "ValueError"
    assert result.evalue == "new"
    assert result.traceback == ["new tb"]
    assert [event.kind for event in result.events] == ["error"]


def test_shell_reply_parser_ignores_non_error_reply() -> None:
    assert output_item_from_shell_reply({"status": "ok"}) is None
    shell_reply = parse_shell_reply_message({"content": {"status": "ok"}})
    assert shell_reply is not None
    assert output_item_from_shell_reply_message(shell_reply) is None


@pytest.mark.parametrize(
    "event",
    [
        ExecutionEvent(kind="stderr", content="warn\n"),
        ExecutionEvent(
            kind="display",
            content="preview",
            metadata={"mime": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
        ),
        ExecutionEvent(kind="status", content="idle"),
    ],
)
def test_accumulator_preserves_selected_legacy_event_shapes(event: ExecutionEvent) -> None:
    accumulator = ExecutionResultAccumulator()
    accumulator.accept(event)

    result = accumulator.build(duration_ms=3)

    assert result.events == [event]


def test_accumulator_drops_invalid_display_metadata_shapes_from_legacy_events() -> None:
    accumulator = ExecutionResultAccumulator()
    event = ExecutionEvent(
        kind="display",
        content="preview",
        metadata={"mime": {"text/plain": "ok", "text/html": 7}},
    )

    accumulator.accept(event)
    result = accumulator.build(duration_ms=3)

    assert result.events == [
        ExecutionEvent(
            kind="display",
            content="preview",
            metadata={"mime": {"text/plain": "ok"}},
        )
    ]


def test_shell_reply_parser_drops_invalid_traceback_metadata_in_legacy_event() -> None:
    item = output_item_from_shell_reply(
        {"status": "error", "ename": "ValueError", "evalue": "boom", "traceback": ["tb", 7]}
    )

    assert item is not None
    assert item.to_event() == ExecutionEvent(
        kind="error",
        content="boom",
        metadata={"ename": "ValueError"},
    )


def test_iopub_message_parser_extracts_parent_id_and_projects_output_item() -> None:
    parsed = parse_iopub_message(
        {
            "msg_type": "display_data",
            "parent_header": {"msg_id": "run-1"},
            "content": {"data": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
        }
    )

    assert parsed is not None
    assert parsed.parent_id == "run-1"
    item = output_item_from_iopub_message(parsed)
    assert item is not None
    assert item.to_event() == ExecutionEvent(
        kind="display",
        content="preview",
        metadata={"mime": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
    )
