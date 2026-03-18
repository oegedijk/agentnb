from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .contracts import ExecutionSink

OutputSelector = Literal["stdout", "stderr", "result"]
StartupPolicy = Literal["default", "always", "never"]


@dataclass(slots=True, frozen=True)
class ExecInvocationPolicy:
    startup_policy: StartupPolicy = "default"
    background: bool = False
    stream: bool = False
    output_selector: OutputSelector | None = None
    no_truncate: bool = False

    @classmethod
    def from_cli(
        cls,
        *,
        startup_policy: StartupPolicy | None,
        background: bool,
        stream: bool,
        output_selector: OutputSelector | None,
        no_truncate: bool = False,
    ) -> ExecInvocationPolicy:
        return cls(
            startup_policy="default" if startup_policy is None else startup_policy,
            background=background,
            stream=stream,
            output_selector=output_selector,
            no_truncate=no_truncate,
        )

    @property
    def ensure_started(self) -> bool:
        return self.startup_policy != "never"

    @property
    def explicitly_ensures_started(self) -> bool:
        return self.startup_policy == "always"

    @property
    def explicitly_disables_startup(self) -> bool:
        return self.startup_policy == "never"

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
