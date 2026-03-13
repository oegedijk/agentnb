from __future__ import annotations

from pathlib import Path

from .contracts import CommandResponse, ExecutionResult, KernelStatus
from .kernel.provisioner import DoctorReport, KernelProvisioner, ProvisionResult
from .payloads import DoctorPayload
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID, resolve_project_root

__version__ = "0.1.1"

__all__ = [
    "CommandResponse",
    "DoctorReport",
    "ExecutionResult",
    "KernelProvisioner",
    "KernelRuntime",
    "KernelStatus",
    "ProvisionResult",
    "__version__",
    "doctor_environment",
    "execute_code",
    "start_kernel",
    "status_kernel",
    "stop_kernel",
]


def start_kernel(
    project: str | Path = ".",
    session_id: str = DEFAULT_SESSION_ID,
    *,
    auto_install: bool = False,
) -> tuple[KernelStatus, bool]:
    runtime = KernelRuntime()
    project_root = resolve_project_root(override=Path(project))
    return runtime.start(
        project_root=project_root,
        session_id=session_id,
        auto_install=auto_install,
    )


def status_kernel(project: str | Path = ".", session_id: str = DEFAULT_SESSION_ID) -> KernelStatus:
    runtime = KernelRuntime()
    project_root = resolve_project_root(override=Path(project))
    return runtime.status(project_root=project_root, session_id=session_id)


def execute_code(
    code: str,
    project: str | Path = ".",
    timeout_s: float = 30.0,
    session_id: str = DEFAULT_SESSION_ID,
) -> ExecutionResult:
    runtime = KernelRuntime()
    project_root = resolve_project_root(override=Path(project))
    return runtime.execute(
        project_root=project_root, session_id=session_id, code=code, timeout_s=timeout_s
    )


def stop_kernel(project: str | Path = ".", session_id: str = DEFAULT_SESSION_ID) -> None:
    runtime = KernelRuntime()
    project_root = resolve_project_root(override=Path(project))
    runtime.stop(project_root=project_root, session_id=session_id)


def doctor_environment(
    project: str | Path = ".",
    *,
    python_executable: str | Path | None = None,
    auto_fix: bool = False,
    session_id: str = DEFAULT_SESSION_ID,
) -> DoctorPayload:
    runtime = KernelRuntime()
    project_root = resolve_project_root(override=Path(project))
    python_path = Path(python_executable) if python_executable is not None else None
    return runtime.doctor(
        project_root=project_root,
        session_id=session_id,
        python_executable=python_path,
        auto_fix=auto_fix,
    )
