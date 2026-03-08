from __future__ import annotations

import subprocess
from pathlib import Path

from pytest_mock import MockerFixture

from agentnb.provisioner import _python_supports_module


def test_python_supports_module_uses_subprocess_probe(mocker: MockerFixture) -> None:
    run_mock = mocker.patch("agentnb.provisioner.subprocess.run")
    run_mock.return_value = subprocess.CompletedProcess(args=["python"], returncode=0)

    assert _python_supports_module(Path("/usr/bin/python3"), "ipykernel_launcher") is True
    run_mock.assert_called_once()
