from __future__ import annotations

from pathlib import Path

from .contracts import ExecutionResult
from .session import SessionInfo


class Hooks:
    """No-op extension hooks for future plugins, policy, and telemetry."""

    def before_execute(self, project_root: Path, session_id: str, code: str) -> None:
        del project_root, session_id, code

    def after_execute(
        self,
        project_root: Path,
        session_id: str,
        code: str,
        result: ExecutionResult | None,
        error: Exception | None,
    ) -> None:
        del project_root, session_id, code, result, error

    def on_kernel_start(self, project_root: Path, session_id: str, session: SessionInfo) -> None:
        del project_root, session_id, session

    def on_kernel_stop(self, project_root: Path, session_id: str, session: SessionInfo) -> None:
        del project_root, session_id, session
