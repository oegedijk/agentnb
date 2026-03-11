from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import ExecutionEvent, ExecutionResult, ExecutionSink


@dataclass(slots=True)
class ExecutionResultAccumulator:
    stdout_parts: list[str] = field(default_factory=list)
    stderr_parts: list[str] = field(default_factory=list)
    events: list[ExecutionEvent] = field(default_factory=list)
    result_text: str | None = None
    execution_count: int | None = None
    status: str = "ok"
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] | None = None

    def accept(self, event: ExecutionEvent) -> None:
        self.events.append(event)

        if event.kind == "stdout":
            self.stdout_parts.append(event.content or "")
            return
        if event.kind == "stderr":
            self.stderr_parts.append(event.content or "")
            return
        if event.kind == "result":
            self.result_text = event.content
            return
        if event.kind == "display":
            if event.content:
                self.result_text = (
                    f"{self.result_text}\n{event.content}" if self.result_text else event.content
                )
            return
        if event.kind == "error":
            self.status = "error"
            self.evalue = event.content
            metadata = event.metadata
            ename = metadata.get("ename")
            if isinstance(ename, str):
                self.ename = ename
            traceback = metadata.get("traceback")
            if isinstance(traceback, list) and all(isinstance(item, str) for item in traceback):
                self.traceback = list(traceback)

    def set_execution_count(self, execution_count: object) -> None:
        if isinstance(execution_count, int):
            self.execution_count = execution_count

    def apply_shell_reply(self, shell_content: dict[str, Any]) -> None:
        if self.execution_count is None:
            self.set_execution_count(shell_content.get("execution_count"))
        if shell_content.get("status") != "error":
            return
        self.status = "error"
        ename = shell_content.get("ename")
        if isinstance(ename, str):
            self.ename = ename
        evalue = shell_content.get("evalue")
        if isinstance(evalue, str):
            self.evalue = evalue
        traceback = shell_content.get("traceback")
        if isinstance(traceback, list) and all(isinstance(item, str) for item in traceback):
            self.traceback = list(traceback)

    def build(self, *, duration_ms: int) -> ExecutionResult:
        return ExecutionResult(
            status="error" if self.status == "error" else "ok",
            stdout="".join(self.stdout_parts),
            stderr="".join(self.stderr_parts),
            result=self.result_text,
            execution_count=self.execution_count,
            duration_ms=duration_ms,
            ename=self.ename,
            evalue=self.evalue,
            traceback=self.traceback,
            events=list(self.events),
        )


def dispatch_event(
    *,
    accumulator: ExecutionResultAccumulator,
    event: ExecutionEvent,
    sink: ExecutionSink | None,
) -> None:
    accumulator.accept(event)
    if sink is not None:
        sink.accept(event)
