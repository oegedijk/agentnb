from __future__ import annotations

from dataclasses import dataclass, field

from .contracts import ExecutionEvent, ExecutionResult, ExecutionSink
from .execution_output import ExecutionOutput, OutputItem, output_item_from_shell_reply_message
from .kernel.jupyter_protocol import ShellReplyMessage


@dataclass(slots=True)
class ExecutionResultAccumulator:
    output: ExecutionOutput = field(default_factory=ExecutionOutput)
    execution_count: int | None = None
    shell_reply_error: OutputItem | None = None

    def accept(self, event: ExecutionEvent) -> None:
        self.output.append(OutputItem.from_event(event))

    def accept_output(self, item: OutputItem) -> None:
        self.output.append(item)

    def set_execution_count(self, execution_count: object) -> None:
        if isinstance(execution_count, int):
            self.execution_count = execution_count

    def apply_shell_reply(self, shell_reply: ShellReplyMessage) -> None:
        if self.execution_count is None:
            self.set_execution_count(shell_reply.execution_count)
        self.shell_reply_error = output_item_from_shell_reply_message(shell_reply)

    def build(self, *, duration_ms: int) -> ExecutionResult:
        ename, evalue, traceback = self.output.error_details()
        if self.shell_reply_error is not None:
            ename = self.shell_reply_error.ename or ename
            evalue = self.shell_reply_error.text or evalue
            traceback = self.shell_reply_error.traceback or traceback
        return ExecutionResult(
            status=self.output.status(),
            stdout=self.output.stdout_text(),
            stderr=self.output.stderr_text(),
            result=self.output.result_text(),
            execution_count=self.execution_count,
            duration_ms=duration_ms,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
            events=self.output.to_events(),
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


def dispatch_output_item(
    *,
    accumulator: ExecutionResultAccumulator,
    item: OutputItem,
    sink: ExecutionSink | None,
) -> None:
    accumulator.accept_output(item)
    if sink is not None:
        sink.accept(item.to_event())
