from __future__ import annotations

import pytest

from agentnb.recording import CommandRecorder


@pytest.mark.parametrize(
    ("recording", "expected"),
    [
        (
            CommandRecorder().exec(code="alpha = 1\nalpha"),
            [
                (
                    "kernel_execution",
                    "internal",
                    "kernel_execution",
                    False,
                    "exec kernel execution",
                ),
                ("user_command", "replayable", "user_command", True, "exec"),
            ],
        ),
        (
            CommandRecorder().reset(),
            [
                ("kernel_execution", "internal", "kernel_execution", False, "reset kernel state"),
                ("user_command", "replayable", "user_command", True, "reset"),
            ],
        ),
        (
            CommandRecorder().vars(code="helper()"),
            [
                ("kernel_execution", "internal", "kernel_execution", False, "vars helper"),
                ("user_command", "inspection", "user_command", True, "vars"),
            ],
        ),
        (
            CommandRecorder().inspect(name="alpha", code="inspect_helper()"),
            [
                ("kernel_execution", "internal", "kernel_execution", False, "inspect alpha helper"),
                ("user_command", "inspection", "user_command", True, "inspect alpha"),
            ],
        ),
        (
            CommandRecorder().reload(module_name="pkg.mod", code="reload_helper()"),
            [
                (
                    "kernel_execution",
                    "internal",
                    "kernel_execution",
                    False,
                    "reload pkg.mod helper",
                ),
                ("user_command", "control", "user_command", True, "reload pkg.mod"),
            ],
        ),
    ],
)
def test_command_recorder_builds_expected_provenance(
    recording,
    expected: list[tuple[str, str, str, bool, str]],
) -> None:
    records = recording.build_records(
        ts="2026-03-20T00:00:00+00:00",
        session_id="default",
        execution_id="run-1",
        status="ok",
        duration_ms=7,
        stdout="hello\n",
        result="  world  ",
    )

    assert [
        (
            record.kind,
            record.classification,
            record.provenance_detail,
            record.user_visible,
            record.label,
        )
        for record in records
    ] == expected
    assert all(record.result_preview == "world" for record in records)
    assert all(record.stdout_preview == "hello" for record in records)


@pytest.mark.parametrize(
    "recording",
    [
        CommandRecorder().exec(code="1 / 0"),
        CommandRecorder().reset(),
        CommandRecorder().vars(code="helper()"),
        CommandRecorder().inspect(name="missing", code="inspect_helper()"),
        CommandRecorder().reload(module_name=None, code="reload_helper()"),
    ],
)
def test_command_recorder_builds_error_records(recording) -> None:
    records = recording.build_records(
        ts="2026-03-20T00:00:00+00:00",
        session_id="default",
        execution_id="run-1",
        status="error",
        duration_ms=9,
        error_type="NameError",
        failure_origin="control",
        stdout="trace\n",
        result="value",
    )

    assert [record.status for record in records] == ["error"] * len(records)
    assert [record.error_type for record in records] == ["NameError"] * len(records)
    assert [record.failure_origin for record in records] == ["control"] * len(records)
    assert all(record.result_preview == "value" for record in records)
    assert all(record.stdout_preview == "trace" for record in records)
