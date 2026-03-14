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
        output = self.output.refined_with_error(self.shell_reply_error)
        ename, evalue, traceback = output.error_details()
        status = output.status()
        if self.shell_reply_error is not None:
            status = "error"
            ename = self.shell_reply_error.ename or ename
            evalue = self.shell_reply_error.text or evalue
            traceback = self.shell_reply_error.traceback or traceback
        return ExecutionResult(
            status=status,
            execution_count=self.execution_count,
            duration_ms=duration_ms,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
            outputs=list(output.items),
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
