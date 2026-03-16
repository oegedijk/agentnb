from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .contracts import ExecutionSink

OutputSelector = Literal["stdout", "stderr", "result"]


@dataclass(slots=True, frozen=True)
class ExecInvocationPolicy:
    ensure_started: bool = False
    background: bool = False
    stream: bool = False
    output_selector: OutputSelector | None = None

    @classmethod
    def from_cli(
        cls,
        *,
        ensure_started: bool,
        background: bool,
        stream: bool,
        output_selector: OutputSelector | None,
    ) -> ExecInvocationPolicy:
        return cls(
            ensure_started=ensure_started,
            background=background,
            stream=stream,
            output_selector=output_selector,
        )

    @property
    def is_background(self) -> bool:
        return self.background

    @property
    def is_stream(self) -> bool:
        return self.stream

    def streaming_sink(self, sink: ExecutionSink | None) -> ExecutionSink | None:
        if self.is_stream:
            return sink
        return None

    def validation_error(self) -> str | None:
        if self.is_background and self.output_selector is not None:
            return "Output selectors are not supported with --background."
        if self.is_background and self.is_stream:
            return "--stream and --background cannot be used together."
        if self.is_stream and self.output_selector is not None:
            return "Output selectors are not supported with --stream."
        return None
