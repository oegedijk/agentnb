from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..payloads import CancelRunResult, RunSnapshot
from .models import RunObserver, RunSpec
from .store import ManagedExecution


class RunManager(Protocol):
    def submit(self, spec: RunSpec, *, observer: RunObserver | None = None) -> ManagedExecution: ...

    def list_runs(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
        errors_only: bool = False,
    ) -> list[RunSnapshot]: ...

    def get_run(self, *, project_root: Path, execution_id: str) -> RunSnapshot: ...

    def wait_for_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> RunSnapshot: ...

    def follow_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
        observer: RunObserver | None = None,
    ) -> RunSnapshot: ...

    def cancel_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> CancelRunResult: ...

    def complete_background_run(self, *, project_root: Path, execution_id: str) -> None: ...
