from __future__ import annotations

from agentnb.contracts import ExecutionEvent, ExecutionSink
from agentnb.execution_events import ExecutionResultAccumulator, dispatch_event


def test_accumulator_preserves_display_event_kind_and_final_result() -> None:
    accumulator = ExecutionResultAccumulator()
    accumulator.accept(ExecutionEvent(kind="result", content="2"))
    accumulator.accept(ExecutionEvent(kind="display", content="table preview"))

    result = accumulator.build(duration_ms=7)

    assert [event.kind for event in result.events] == ["result", "display"]
    assert result.result == "2\ntable preview"


def test_dispatch_event_forwards_to_sink() -> None:
    accumulator = ExecutionResultAccumulator()

    class Sink(ExecutionSink):
        def __init__(self) -> None:
            self.started_calls: list[tuple[str, str]] = []
            self.events: list[ExecutionEvent] = []

        def started(self, *, execution_id: str, session_id: str) -> None:
            self.started_calls.append((execution_id, session_id))

        def accept(self, event: ExecutionEvent) -> None:
            self.events.append(event)

    sink = Sink()
    event = ExecutionEvent(kind="stdout", content="hello\n")

    dispatch_event(accumulator=accumulator, event=event, sink=sink)

    result = accumulator.build(duration_ms=3)
    assert sink.events == [event]
    assert result.stdout == "hello\n"
