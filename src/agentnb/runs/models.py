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

    def to_plan(self) -> RunPlan:
        if self.command_type == "exec":
            return RunPlan.for_exec(
                project_root=self.project_root,
                session_id=self.session_id,
                code=self.code or "",
                mode=self.mode,
                timeout_s=self.timeout_s,
                ensure_started=self.ensure_started,
            )
        if self.command_type == "reset":
            return RunPlan.for_reset(
                project_root=self.project_root,
                session_id=self.session_id,
                mode=self.mode,
                timeout_s=self.timeout_s,
            )
        raise ValueError(f"Unsupported run command type: {self.command_type}")


@dataclass(slots=True, frozen=True, kw_only=True)
class RunPlan:
    project_root: Path
    session_id: str
    command_type: RunCommandType
    code: str | None
    mode: RunMode
    timeout_s: float = 30.0
    ensure_started: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())

    @property
    def supports_background(self) -> bool:
        return self.command_type == "exec"

    @classmethod
    def for_exec(
        cls,
        *,
        project_root: Path,
        session_id: str,
        code: str,
        mode: RunMode,
        timeout_s: float,
        ensure_started: bool = False,
    ) -> RunPlan:
        return cls(
            project_root=project_root,
            session_id=session_id,
            command_type="exec",
            code=code,
            mode=mode,
            timeout_s=timeout_s,
            ensure_started=ensure_started,
        )

    @classmethod
    def for_reset(
        cls,
        *,
        project_root: Path,
        session_id: str,
        mode: RunMode,
        timeout_s: float,
    ) -> RunPlan:
        return cls(
            project_root=project_root,
            session_id=session_id,
            command_type="reset",
            code=None,
            mode=mode,
            timeout_s=timeout_s,
        )


@dataclass(slots=True, frozen=True)
class RunHandle:
    execution_id: str
    session_id: str
    command_type: str


class RunObserver(Protocol):
    def started(self, *, execution_id: str, session_id: str) -> None: ...

    def accept(self, event: ExecutionEvent) -> None: ...
