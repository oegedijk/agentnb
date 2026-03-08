from __future__ import annotations

from pathlib import Path

import pytest

from agentnb.errors import ExecutionTimedOutError
from agentnb.runtime import KernelRuntime

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


def test_runtime_start_status_stop(runtime: KernelRuntime, project_dir: Path) -> None:
    status, started_new = runtime.start(project_dir)
    assert status.alive is True
    assert started_new is True

    current_status = runtime.status(project_dir)
    assert current_status.alive is True

    runtime.stop(project_dir)
    stopped_status = runtime.status(project_dir)
    assert stopped_status.alive is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("x = 41\nx + 1", "42"),
        ("sum([1, 2, 3])", "6"),
    ],
)
def test_runtime_execute_in_running_kernel(
    started_runtime: tuple[KernelRuntime, Path],
    code: str,
    expected: str,
) -> None:
    runtime, project_dir = started_runtime
    execution = runtime.execute(project_root=project_dir, code=code, timeout_s=10)
    assert execution.status == "ok"
    assert execution.result == expected


def test_runtime_timeout_interrupt_leaves_kernel_usable(
    started_runtime: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime

    with pytest.raises(ExecutionTimedOutError):
        runtime.execute(project_root=project_dir, code="import time\ntime.sleep(3)", timeout_s=0.2)

    follow_up = runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=5)
    assert follow_up.status == "ok"
    assert follow_up.result == "2"


def test_runtime_reset_clears_namespace(started_runtime: tuple[KernelRuntime, Path]) -> None:
    runtime, project_dir = started_runtime
    runtime.execute(project_root=project_dir, code="x = 123", timeout_s=5)

    runtime.reset(project_root=project_dir, timeout_s=10)
    after_reset = runtime.execute(project_root=project_dir, code="x", timeout_s=5)

    assert after_reset.status == "error"
    assert after_reset.ename in {"NameError", "KeyError"}
