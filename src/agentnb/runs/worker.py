from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_ACTIVE_RUN_STATUSES = frozenset({"starting", "running"})


@dataclass(slots=True, frozen=True, kw_only=True)
class BackgroundWorkerRequest:
    project_root: Path
    execution_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())

    def to_argv(self) -> list[str]:
        return [
            "--project-root",
            str(self.project_root),
            "--execution-id",
            self.execution_id,
        ]


class BackgroundWorkerArgumentError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        project_root: Path | None = None,
        execution_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.project_root = project_root.resolve() if project_root is not None else None
        self.execution_id = execution_id


def parse_argv(argv: list[str]) -> BackgroundWorkerRequest:
    project_root: Path | None = None
    execution_id: str | None = None
    index = 0

    while index < len(argv):
        token = argv[index]
        index += 1

        if token == "--project-root":
            if project_root is not None:
                raise BackgroundWorkerArgumentError(
                    "Duplicate argument: --project-root.",
                    project_root=project_root,
                    execution_id=execution_id,
                )
            if index >= len(argv) or not argv[index]:
                raise BackgroundWorkerArgumentError(
                    "Missing value for --project-root.",
                    project_root=project_root,
                    execution_id=execution_id,
                )
            project_root = Path(argv[index])
            index += 1
            continue

        if token == "--execution-id":
            if execution_id is not None:
                raise BackgroundWorkerArgumentError(
                    "Duplicate argument: --execution-id.",
                    project_root=project_root,
                    execution_id=execution_id,
                )
            if index >= len(argv) or not argv[index]:
                raise BackgroundWorkerArgumentError(
                    "Missing value for --execution-id.",
                    project_root=project_root,
                    execution_id=execution_id,
                )
            execution_id = argv[index]
            index += 1
            continue

        raise BackgroundWorkerArgumentError(
            f"Unexpected argument: {token}",
            project_root=project_root,
            execution_id=execution_id,
        )

    if project_root is None:
        raise BackgroundWorkerArgumentError(
            "Missing required argument: --project-root.",
            execution_id=execution_id,
        )
    if execution_id is None:
        raise BackgroundWorkerArgumentError(
            "Missing required argument: --execution-id.",
            project_root=project_root,
        )

    return BackgroundWorkerRequest(project_root=project_root, execution_id=execution_id)


def run_background_worker(request: BackgroundWorkerRequest) -> None:
    from ..runtime import KernelRuntime
    from .local_manager import LocalRunManager

    runtime = KernelRuntime()
    manager = LocalRunManager(runtime)
    manager.complete_background_run(
        project_root=request.project_root,
        execution_id=request.execution_id,
    )


def _record_boot_failure(error: BackgroundWorkerArgumentError) -> None:
    if error.project_root is None or error.execution_id is None:
        return

    from ..recording import CommandRecorder
    from .store import ExecutionRun, ExecutionStore

    store = ExecutionStore(error.project_root)
    record = store.get(error.execution_id)
    if record is None or record.status not in _ACTIVE_RUN_STATUSES:
        return

    run = ExecutionRun(
        store=store,
        record=record,
        recording=CommandRecorder().for_execution(
            command_type=record.command_type,
            code=record.code,
        ),
        started=True,
    )
    updated = run.error_record(error)
    latest = store.get(error.execution_id)
    if latest is None or latest.status not in _ACTIVE_RUN_STATUSES:
        return
    run.replace(
        status=updated.status,
        duration_ms=updated.duration_ms,
        stdout=updated.stdout,
        stderr=updated.stderr,
        result=updated.result,
        execution_count=updated.execution_count,
        ename=updated.ename,
        evalue=updated.evalue,
        traceback=updated.traceback,
        outputs=updated.outputs,
        events=updated.events,
        journal_entries=updated.journal_entries,
    )


def main(argv: list[str] | None = None) -> int:
    try:
        request = parse_argv(list(sys.argv[1:] if argv is None else argv))
    except BackgroundWorkerArgumentError as error:
        _record_boot_failure(error)
        return 1
    run_background_worker(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
