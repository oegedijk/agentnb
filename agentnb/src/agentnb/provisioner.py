from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .errors import InvalidInputError, ProvisioningError

IPYKERNEL_REQUIREMENT = "ipykernel>=6.0"

CheckStatus = Literal["ok", "warn", "error"]


@dataclass(slots=True)
class InterpreterSelection:
    executable: str
    source: str
    ipykernel_available: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "executable": self.executable,
            "source": self.source,
            "ipykernel_available": self.ipykernel_available,
        }


@dataclass(slots=True)
class ProvisionResult:
    executable: str
    source: str
    installed_ipykernel: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "executable": self.executable,
            "source": self.source,
            "installed_ipykernel": self.installed_ipykernel,
        }


@dataclass(slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    message: str
    fix_hint: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "fix_hint": self.fix_hint,
        }


@dataclass(slots=True)
class DoctorReport:
    ready: bool
    selected_python: str | None
    python_source: str | None
    checks: list[DoctorCheck]

    def to_dict(self) -> dict[str, object]:
        return {
            "ready": self.ready,
            "selected_python": self.selected_python,
            "python_source": self.python_source,
            "checks": [check.to_dict() for check in self.checks],
        }


class KernelProvisioner:
    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()

    def select_interpreter(self, preferred_python: Path | None = None) -> InterpreterSelection:
        candidates = self._candidate_interpreters(preferred_python=preferred_python)
        for executable, source in candidates:
            if executable.exists():
                python = str(executable)
                return InterpreterSelection(
                    executable=python,
                    source=source,
                    ipykernel_available=_python_supports_module(executable, "ipykernel_launcher"),
                )

        raise ProvisioningError(
            "Could not find a usable Python interpreter. "
            "Use --python to provide an explicit interpreter path."
        )

    def provision(
        self,
        preferred_python: Path | None = None,
        auto_install: bool = True,
    ) -> ProvisionResult:
        selected = self.select_interpreter(preferred_python=preferred_python)
        installed = self.ensure_ipykernel(selected, auto_install=auto_install)
        return ProvisionResult(
            executable=selected.executable,
            source=selected.source,
            installed_ipykernel=installed,
        )

    def ensure_ipykernel(self, selected: InterpreterSelection, auto_install: bool) -> bool:
        if selected.ipykernel_available:
            return False

        install_cmd = [selected.executable, "-m", "pip", "install", IPYKERNEL_REQUIREMENT]
        install_cmd_text = " ".join(install_cmd)

        if not auto_install:
            raise ProvisioningError(
                f"Selected interpreter is missing ipykernel. Install it with: {install_cmd_text}"
            )

        result = subprocess.run(
            install_cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode != 0:
            detail = _tail_text(result.stderr or result.stdout)
            raise ProvisioningError(
                "Failed to auto-install ipykernel. "
                f"Try running manually: {install_cmd_text}. "
                f"Installer output: {detail}"
            )

        if not _python_supports_module(Path(selected.executable), "ipykernel_launcher"):
            raise ProvisioningError(
                "ipykernel install completed but module is still unavailable. "
                f"Try running manually: {install_cmd_text}"
            )

        return True

    def doctor(self, preferred_python: Path | None = None, auto_fix: bool = False) -> DoctorReport:
        checks: list[DoctorCheck] = []

        try:
            selected = self.select_interpreter(preferred_python=preferred_python)
        except (InvalidInputError, ProvisioningError) as exc:
            checks.append(
                DoctorCheck(
                    name="python",
                    status="error",
                    message=exc.message if isinstance(exc, ProvisioningError) else str(exc),
                    fix_hint="Provide a valid Python path with --python.",
                )
            )
            return DoctorReport(
                ready=False,
                selected_python=None,
                python_source=None,
                checks=checks,
            )

        checks.append(
            DoctorCheck(
                name="python",
                status="ok",
                message=f"Using interpreter: {selected.executable} ({selected.source})",
            )
        )

        if selected.ipykernel_available:
            checks.append(
                DoctorCheck(
                    name="ipykernel",
                    status="ok",
                    message="ipykernel is installed.",
                )
            )
        else:
            install_cmd = f"{selected.executable} -m pip install {IPYKERNEL_REQUIREMENT}"
            if auto_fix:
                try:
                    self.ensure_ipykernel(selected, auto_install=True)
                    checks.append(
                        DoctorCheck(
                            name="ipykernel",
                            status="ok",
                            message="ipykernel was missing and has been installed.",
                        )
                    )
                except ProvisioningError as exc:
                    checks.append(
                        DoctorCheck(
                            name="ipykernel",
                            status="error",
                            message=exc.message,
                            fix_hint=f"Run manually: {install_cmd}",
                        )
                    )
            else:
                checks.append(
                    DoctorCheck(
                        name="ipykernel",
                        status="warn",
                        message="ipykernel is not installed for the selected interpreter.",
                        fix_hint=f"Run: {install_cmd}",
                    )
                )

        checks.append(self._check_state_directory())
        checks.append(self._check_socket_bind())

        ready = all(check.status == "ok" for check in checks)
        return DoctorReport(
            ready=ready,
            selected_python=selected.executable,
            python_source=selected.source,
            checks=checks,
        )

    def _candidate_interpreters(self, preferred_python: Path | None) -> list[tuple[Path, str]]:
        if preferred_python is not None:
            path = preferred_python.expanduser().absolute()
            if not path.exists():
                raise InvalidInputError(f"Python interpreter not found: {path}")
            return [(path, "explicit")]

        candidates: list[tuple[Path, str]] = []
        candidates.extend(
            [
                (self.project_root / ".venv" / "bin" / "python", "project_venv"),
                (self.project_root / ".venv" / "Scripts" / "python.exe", "project_venv"),
            ]
        )

        active_venv = os.environ.get("VIRTUAL_ENV")
        if active_venv:
            active = Path(active_venv)
            candidates.extend(
                [
                    (active / "bin" / "python", "active_venv"),
                    (active / "Scripts" / "python.exe", "active_venv"),
                ]
            )

        candidates.append((Path(sys.executable).absolute(), "current_python"))

        unique: list[tuple[Path, str]] = []
        seen: set[str] = set()
        for candidate, source in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            unique.append((candidate, source))
        return unique

    def _check_state_directory(self) -> DoctorCheck:
        state_dir = self.project_root / ".agentnb"
        try:
            state_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=state_dir, prefix="doctor-", suffix=".tmp", delete=True
            ):
                pass
            return DoctorCheck(
                name="state_dir",
                status="ok",
                message=f"State directory is writable: {state_dir}",
            )
        except OSError as exc:
            return DoctorCheck(
                name="state_dir",
                status="error",
                message=f"Cannot write to state directory {state_dir}: {exc}",
                fix_hint="Check directory permissions.",
            )

    def _check_socket_bind(self) -> DoctorCheck:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", 0))
            return DoctorCheck(
                name="socket",
                status="ok",
                message="Loopback socket bind succeeded.",
            )
        except OSError as exc:
            return DoctorCheck(
                name="socket",
                status="error",
                message=f"Unable to bind loopback socket: {exc}",
                fix_hint="Check local firewall/sandbox/network policy.",
            )
        finally:
            sock.close()


def _python_supports_module(executable: Path, module_name: str) -> bool:
    try:
        completed = subprocess.run(
            [str(executable), "-c", f"import {module_name}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _tail_text(text: str, max_chars: int = 400) -> str:
    stripped = text.strip()
    if len(stripped) <= max_chars:
        return stripped
    return stripped[-max_chars:]
