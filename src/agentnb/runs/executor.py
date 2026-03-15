from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from ..contracts import ExecutionEvent, ExecutionResult, ExecutionSink
from .models import RunObserver, RunPlan
from .store import ExecutionRecord, ExecutionRun

if TYPE_CHECKING:
    from ..runtime import KernelRuntime


class RunExecutor(Protocol):
    def run_foreground(
        self,
        *,
        plan: RunPlan,
        run: ExecutionRun,
        observer: RunObserver | None,
    ) -> ExecutionResult: ...

    def start_background(
        self,
        *,
        plan: RunPlan,
        run: ExecutionRun,
    ) -> ExecutionRecord: ...

    def complete_background_run(
        self,
        *,
        plan: RunPlan,
        run: ExecutionRun,
    ) -> ExecutionResult: ...


class LocalRunExecutor:
    def __init__(self, runtime: KernelRuntime) -> None:
        self.runtime = runtime

    def run_foreground(
        self,
        *,
        plan: RunPlan,
        run: ExecutionRun,
        observer: RunObserver | None,
    ) -> ExecutionResult:
        execution_sink = observer if observer is not None else None
        if plan.command_type == "exec":
            return self.runtime.execute(
                project_root=plan.project_root,
                session_id=plan.session_id,
                code=plan.code or "",
                timeout_s=plan.timeout_s,
                before_backend=lambda: run.start(observer),
                event_sink=execution_sink,
            )
        if plan.command_type == "reset":
            return self.runtime.reset(
                project_root=plan.project_root,
                session_id=plan.session_id,
                timeout_s=plan.timeout_s,
            )
        raise ValueError(f"Unsupported run plan command type: {plan.command_type}")

    def start_background(
        self,
        *,
        plan: RunPlan,
        run: ExecutionRun,
    ) -> ExecutionRecord:
        if not plan.supports_background:
            raise ValueError(f"Unsupported run mode for {plan.command_type}: {plan.mode}")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "agentnb.cli",
                "_background-run",
                "--project",
                str(plan.project_root),
                run.record.execution_id,
            ],
            cwd=str(plan.project_root),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return replace(
            run.record,
            status="running",
            worker_pid=process.pid,
        )

    def complete_background_run(
        self,
        *,
        plan: RunPlan,
        run: ExecutionRun,
    ) -> ExecutionResult:
        if not plan.supports_background:
            raise ValueError(f"Unsupported run mode for {plan.command_type}: {plan.mode}")
        if run.record.status != "running" or run.record.worker_pid != os.getpid():
            run.replace(status="running", worker_pid=os.getpid())
        progress_sink = _ExecutionProgressSink(run)
        return self.runtime.execute(
            project_root=plan.project_root,
            session_id=plan.session_id,
            code=plan.code or "",
            timeout_s=plan.timeout_s,
            event_sink=progress_sink,
        )


class _ExecutionProgressSink(ExecutionSink):
    def __init__(self, run: ExecutionRun) -> None:
        from ..execution_events import ExecutionResultAccumulator

        self._run = run
        self._accumulator = ExecutionResultAccumulator()

    def started(self, *, execution_id: str, session_id: str) -> None:
        del execution_id, session_id

    def accept(self, event: ExecutionEvent) -> None:
        self._accumulator.accept(event)
        snapshot = self._accumulator.build(duration_ms=0)
        status = "error" if snapshot.status == "error" else "running"

        self._run.replace(
            status=status,
            stdout=snapshot.stdout,
            stderr=snapshot.stderr,
            result=snapshot.result,
            ename=snapshot.ename,
            evalue=snapshot.evalue,
            traceback=snapshot.traceback,
            outputs=list(snapshot.outputs),
            events=list(snapshot.events),
        )
