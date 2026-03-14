from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from ..contracts import ExecutionEvent

RunCommandType = Literal["exec", "reset"]
RunMode = Literal["foreground", "background"]


@dataclass(slots=True, frozen=True, kw_only=True)
class RunSpec:
    project_root: Path
    session_id: str
    command_type: RunCommandType
    code: str | None
    mode: RunMode
    timeout_s: float = 30.0
    ensure_started: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())


@dataclass(slots=True, frozen=True)
class RunHandle:
    execution_id: str
    session_id: str
    command_type: str


class RunObserver(Protocol):
    def started(self, *, execution_id: str, session_id: str) -> None: ...

    def accept(self, event: ExecutionEvent) -> None: ...
