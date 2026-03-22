from __future__ import annotations

from pathlib import Path

from pytest_mock import MockerFixture

from agentnb.contracts import ExecutionResult, KernelStatus
from agentnb.kernel.backend import BackendCapabilities, RuntimeBackend
from agentnb.kernel.provisioner import DoctorCheck, DoctorReport, ProvisionResult
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo, SessionStore
from agentnb.state import SessionStateFiles


class EchoSessionBackend(RuntimeBackend):
    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities()

    def start(
        self,
        project_root: Path,
        session_state: SessionStateFiles,
        python_executable: str,
    ) -> SessionInfo:
        return SessionInfo(
            session_id=session_state.session_id,
            pid=12345,
            connection_file=str(session_state.connection_file),
            python_executable=python_executable,
            project_root=str(project_root),
            started_at="2026-03-08T00:00:00+00:00",
        )

    def status(self, session: SessionInfo, timeout_s: float = 2.0) -> KernelStatus:
        del timeout_s
        return KernelStatus(alive=True, pid=session.pid, python=session.python_executable)

    def execute(
        self,
        session: SessionInfo,
        code: str,
        timeout_s: float,
        event_sink=None,
    ) -> ExecutionResult:
        del session, code, timeout_s, event_sink
        raise AssertionError("execute should not be called in this test")

    def interrupt(self, session: SessionInfo) -> None:
        del session
        raise AssertionError("interrupt should not be called in this test")

    def stop(self, session: SessionInfo, timeout_s: float = 5.0) -> None:
        del session, timeout_s
        raise AssertionError("stop should not be called in this test")

    def reset(self, session: SessionInfo, timeout_s: float) -> ExecutionResult:
        del session, timeout_s
        raise AssertionError("reset should not be called in this test")


def test_runtime_start_uses_provisioner_and_passes_python(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    backend = mocker.Mock()
    session = SessionInfo(
        session_id="default",
        pid=12345,
        connection_file=str(project_dir / ".agentnb" / "kernel-default.json"),
        python_executable="/custom/python",
        project_root=str(project_dir),
        started_at="2026-03-08T00:00:00+00:00",
    )
    backend.start.return_value = session
    backend.status.return_value = KernelStatus(alive=True, pid=12345, python="/custom/python")

    provisioner = mocker.Mock()
    provisioner.provision.return_value = ProvisionResult(
        executable="/custom/python",
        source="explicit",
        installed_ipykernel=True,
    )

    runtime = KernelRuntime(backend=backend, provisioner_factory=lambda _: provisioner)
    status, started_new = runtime.start(
        project_root=project_dir,
        python_executable=Path("/custom/python"),
    )

    assert started_new is True
    assert status.alive is True
    provisioner.provision.assert_called_once_with(
        preferred_python=Path("/custom/python"),
    )
    backend.start.assert_called_once()
    assert backend.start.call_args.kwargs["python_executable"] == "/custom/python"


def test_runtime_start_persists_canonicalized_session_id(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    provisioner = mocker.Mock()
    provisioner.provision.return_value = ProvisionResult(
        executable="/custom/python",
        source="explicit",
        installed_ipykernel=True,
    )

    runtime = KernelRuntime(
        backend=EchoSessionBackend(),
        provisioner_factory=lambda _: provisioner,
    )

    status, started_new = runtime.start(
        project_root=project_dir,
        session_id=" default ",
    )

    persisted = SessionStore(project_dir, session_id="default").load_session()

    assert started_new is True
    assert status.alive is True
    assert persisted is not None
    assert persisted.session_id == "default"


def test_runtime_start_provisions_without_auto_install_contract(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    backend = mocker.Mock()
    session = SessionInfo(
        session_id="default",
        pid=12345,
        connection_file=str(project_dir / ".agentnb" / "kernel-default.json"),
        python_executable="/custom/python",
        project_root=str(project_dir),
        started_at="2026-03-09T00:00:00+00:00",
    )
    backend.start.return_value = session
    backend.status.return_value = KernelStatus(alive=True, pid=12345, python="/custom/python")

    provisioner = mocker.Mock()
    provisioner.provision.return_value = ProvisionResult(
        executable="/custom/python",
        source="explicit",
        installed_ipykernel=False,
    )

    runtime = KernelRuntime(backend=backend, provisioner_factory=lambda _: provisioner)
    runtime.start(project_root=project_dir)

    provisioner.provision.assert_called_once_with(
        preferred_python=None,
    )


def test_runtime_doctor_merges_store_metadata(project_dir: Path, mocker: MockerFixture) -> None:
    backend = mocker.Mock()
    provisioner = mocker.Mock()
    provisioner.doctor.return_value = DoctorReport(
        ready=True,
        selected_python="/custom/python",
        python_source="explicit",
        checks=[DoctorCheck(name="python", status="ok", message="ok")],
    )

    runtime = KernelRuntime(backend=backend, provisioner_factory=lambda _: provisioner)

    payload = runtime.doctor(project_root=project_dir)

    assert payload["ready"] is True
    assert payload["selected_python"] == "/custom/python"
    assert payload["python_source"] == "explicit"
    assert payload["session_exists"] is False
    assert payload["stale_session_cleaned"] is False
    assert payload["checks"] == [
        {"name": "python", "status": "ok", "message": "ok", "fix_hint": None}
    ]


def test_runtime_ensure_started_delegates_to_start(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    runtime = KernelRuntime()
    start_mock = mocker.patch.object(runtime, "start")
    status = KernelStatus(alive=True, pid=123)
    start_mock.return_value = (status, True)

    ensured = runtime.ensure_started(project_root=project_dir, session_id="analysis")

    assert ensured == (status, True)
    start_mock.assert_called_once_with(project_root=project_dir, session_id="analysis")
