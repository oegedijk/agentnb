from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..contracts import HelperAccessMetadata
from .models import RunCancelOutcome, RunHandle, RunObservationResult, RunObserver, RunSpec
from .store import ExecutionRecord, ManagedExecution, RunSelectorCandidate


class RunManager(Protocol):
    def submit(self, spec: RunSpec, *, observer: RunObserver | None = None) -> ManagedExecution: ...

    def list_runs(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
        errors_only: bool = False,
    ) -> list[ExecutionRecord]: ...

    def list_run_selector_candidates(
        self,
        *,
        project_root: Path,
        session_id: str | None = None,
    ) -> list[RunSelectorCandidate]: ...

    def get_run(self, *, project_root: Path, execution_id: str) -> ExecutionRecord: ...

    def wait_for_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
    ) -> ExecutionRecord: ...

    def follow_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 30.0,
        poll_interval_s: float = 0.1,
        observer: RunObserver | None = None,
        skip_history: bool = False,
    ) -> RunObservationResult: ...

    def cancel_run(
        self,
        *,
        project_root: Path,
        execution_id: str,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> RunCancelOutcome: ...

    def wait_for_helper_session_access(
        self,
        *,
        project_root: Path,
        session_id: str,
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.1,
    ) -> HelperAccessMetadata: ...

    def active_run_for_session(
        self,
        *,
        project_root: Path,
        session_id: str,
        excluding_execution_id: str | None = None,
    ) -> RunHandle | None: ...

    def complete_background_run(self, *, project_root: Path, execution_id: str) -> None: ...
