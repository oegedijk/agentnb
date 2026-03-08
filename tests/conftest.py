from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from agentnb.runtime import KernelRuntime


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """
[project]
name = "fixture-project"
version = "0.0.0"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return project


@pytest.fixture
def runtime() -> KernelRuntime:
    return KernelRuntime()


@pytest.fixture
def started_runtime(runtime: KernelRuntime, project_dir: Path) -> tuple[KernelRuntime, Path]:
    runtime.start(project_dir)
    try:
        yield runtime, project_dir
    finally:
        if runtime.status(project_dir).alive:
            runtime.stop(project_dir)


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()
