from __future__ import annotations

from pathlib import Path

import pytest

from agentnb.advice import AdviceContext, AdvicePolicy, _extract_module_name


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (
            AdviceContext(
                command_name="status",
                response_status="ok",
                data={"alive": True, "busy": True},
            ),
            ["Run `agentnb wait --json` to wait until the session is usable."],
        ),
        (
            AdviceContext(
                command_name="status",
                response_status="ok",
                data={"alive": False},
            ),
            [
                "Run `agentnb start --json` to start a project-scoped kernel.",
                "Run `agentnb doctor --json` if startup has been failing.",
            ],
        ),
        (
            AdviceContext(
                command_name="exec",
                response_status="ok",
                data={"background": True},
            ),
            [
                "Run `agentnb runs wait EXECUTION_ID --json` to wait for the final result.",
                "Run `agentnb runs show EXECUTION_ID --json` to inspect the current run record.",
                "Run `agentnb runs cancel EXECUTION_ID --json` to stop the background run.",
            ],
        ),
    ],
)
def test_advice_policy_returns_expected_suggestions(
    context: AdviceContext,
    expected: list[str],
) -> None:
    policy = AdvicePolicy()

    assert policy.suggestions(context) == expected


def test_advice_policy_handles_ambiguous_session_error() -> None:
    policy = AdvicePolicy()
    context = AdviceContext(
        command_name="status",
        response_status="error",
        data={},
        error_code="AMBIGUOUS_SESSION",
    )

    assert policy.suggestions(context) == [
        "Run `agentnb sessions list --json` to see the live session names.",
        "Retry with `agentnb status --session NAME --json` to target one explicitly.",
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "command",
            "label": "List sessions",
            "command": "agentnb",
            "args": ["sessions", "list", "--json"],
        },
        {
            "kind": "command",
            "label": "Retry with --session",
            "command": "agentnb",
            "args": ["status", "--session", "NAME", "--json"],
        },
    ]


def test_advice_policy_uses_session_name_for_preserved_run_cancel() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="runs-cancel",
            response_status="ok",
            data={
                "cancel_requested": True,
                "session_outcome": "preserved",
                "session_id": "analysis",
            },
        )
    )

    assert suggestions == [
        (
            "Run `agentnb wait --session analysis --json` "
            "to confirm the session is ready for more work."
        ),
        "Run `agentnb runs show EXECUTION_ID --json` to inspect the cancelled run record.",
    ]


def test_advice_policy_interpolates_execution_id_for_background_exec() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="ok",
            data={"background": True, "execution_id": "run-7"},
        )
    )

    assert suggestions == [
        "Run `agentnb runs wait run-7 --json` to wait for the final result.",
        "Run `agentnb runs show run-7 --json` to inspect the current run record.",
        "Run `agentnb runs cancel run-7 --json` to stop the background run.",
    ]


def test_advice_policy_background_exec_text_and_actions_share_the_same_steps() -> None:
    policy = AdvicePolicy()
    context = AdviceContext(
        command_name="exec",
        response_status="ok",
        data={"background": True, "execution_id": "run-7"},
    )

    assert policy.suggestions(context) == [
        "Run `agentnb runs wait run-7 --json` to wait for the final result.",
        "Run `agentnb runs show run-7 --json` to inspect the current run record.",
        "Run `agentnb runs cancel run-7 --json` to stop the background run.",
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "command",
            "label": "Wait for run",
            "command": "agentnb",
            "args": ["runs", "wait", "run-7", "--json"],
        },
        {
            "kind": "command",
            "label": "Show run",
            "command": "agentnb",
            "args": ["runs", "show", "run-7", "--json"],
        },
        {
            "kind": "command",
            "label": "Cancel run",
            "command": "agentnb",
            "args": ["runs", "cancel", "run-7", "--json"],
        },
    ]


def test_advice_policy_preserves_cross_project_scope_for_follow_ups() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="ok",
            data={"execution_id": "run-7"},
            session_id="analysis",
            session_source="explicit",
            project_override=Path("/tmp/other"),
        )
    )

    assert suggestions == [
        (
            "Run `agentnb vars --session analysis --recent 5 --project /tmp/other --json` "
            "to inspect namespace changes."
        ),
        (
            "Run `agentnb history --session analysis run-7 --project /tmp/other --json` "
            "to review this execution."
        ),
    ]


def test_advice_policy_module_not_found_error_suggests_install() -> None:
    policy = AdvicePolicy()

    context = AdviceContext(
        command_name="exec",
        response_status="error",
        data={},
        error_code="EXECUTION_ERROR",
        error_name="ModuleNotFoundError",
        error_value="No module named 'pandas'",
    )

    assert policy.suggestions(context) == [
        "Install the missing module: run `uv add pandas` in your shell (not inside the session).",
        "Then retry the execution.",
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "shell",
            "label": "Install dependency",
            "command": "uv",
            "args": ["add", "pandas"],
        }
    ]


def test_advice_policy_pipless_called_process_suggests_uv_add() -> None:
    policy = AdvicePolicy()

    context = AdviceContext(
        command_name="exec",
        response_status="error",
        data={"stderr": "/tmp/.venv/bin/python: No module named pip\n"},
        error_code="EXECUTION_ERROR",
        error_name="CalledProcessError",
        error_value=(
            "Command '['/tmp/.venv/bin/python', '-m', 'pip', 'install', 'pyjokes']' "
            "returned non-zero exit status 1."
        ),
    )

    assert policy.suggestions(context) == [
        "The selected interpreter does not provide pip inside the live session.",
        (
            "Install the dependency from this project with "
            "run `uv add pyjokes` in your shell (not inside the session)."
        ),
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "shell",
            "label": "Install dependency",
            "command": "uv",
            "args": ["add", "pyjokes"],
        }
    ]


def test_advice_policy_module_not_found_extracts_top_level_package() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="error",
            data={},
            error_code="EXECUTION_ERROR",
            error_name="ModuleNotFoundError",
            error_value="No module named 'sklearn.ensemble'",
        )
    )

    assert suggestions == [
        (
            "Install the missing module: run `uv add scikit-learn` "
            "in your shell (not inside the session)."
        ),
        "Then retry the execution.",
    ]


def test_advice_policy_module_not_found_prefers_live_session_repair_when_python_known() -> None:
    policy = AdvicePolicy()

    context = AdviceContext(
        command_name="exec",
        response_status="error",
        data={"session_python": "/tmp/project/.venv/bin/python"},
        error_code="EXECUTION_ERROR",
        error_name="ModuleNotFoundError",
        error_value="No module named 'pandas'",
    )

    assert policy.suggestions(context) == [
        (
            "Repair the live session: run `uv pip install --python "
            "/tmp/project/.venv/bin/python pandas` in your shell."
        ),
        "For a durable project dependency, run `uv add pandas` in your shell.",
        "Then retry the execution.",
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "shell",
            "label": "Repair live session",
            "command": "uv",
            "args": ["pip", "install", "--python", "/tmp/project/.venv/bin/python", "pandas"],
        },
        {
            "kind": "shell",
            "label": "Add dependency",
            "command": "uv",
            "args": ["add", "pandas"],
        },
    ]


def test_advice_policy_pipless_called_process_prefers_uv_pip_when_python_known() -> None:
    policy = AdvicePolicy()

    context = AdviceContext(
        command_name="exec",
        response_status="error",
        data={
            "stderr": "/tmp/.venv/bin/python: No module named pip\n",
            "session_python": "/tmp/.venv/bin/python",
        },
        error_code="EXECUTION_ERROR",
        error_name="CalledProcessError",
        error_value=(
            "Command '['/tmp/.venv/bin/python', '-m', 'pip', 'install', 'pyjokes']' "
            "returned non-zero exit status 1."
        ),
    )

    assert policy.suggestions(context) == [
        "The selected interpreter does not provide pip inside the live session.",
        (
            "Install the dependency from this project with "
            "run `uv pip install --python /tmp/.venv/bin/python pyjokes` in your shell."
        ),
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "shell",
            "label": "Repair live session",
            "command": "uv",
            "args": ["pip", "install", "--python", "/tmp/.venv/bin/python", "pyjokes"],
        }
    ]


def test_advice_policy_name_error_with_session_suggests_vars() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="error",
            data={},
            error_code="EXECUTION_ERROR",
            error_name="NameError",
            error_value="name 'df' is not defined",
            session_id="analysis",
        )
    )

    assert suggestions == [
        "Run `agentnb vars --session analysis --json` to inspect the namespace.",
        "Run `agentnb sessions list --json` to see all live sessions.",
        "Run `agentnb history --session analysis @last-error --json` to review the latest failure.",
    ]


def test_advice_policy_timeout_uses_runtime_recovery_facts() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="error",
            data={
                "current_runtime_state": "ready",
                "session_busy": False,
                "interrupt_recommended": False,
                "active_execution_id": None,
            },
            error_code="TIMEOUT",
            error_name="TimeoutError",
        )
    )

    assert suggestions == [
        "Run `agentnb history @last-error --json` to review the latest failure.",
        "Run `agentnb reset --json` if the namespace needs a clean slate.",
    ]


def test_advice_policy_doctor_ready_with_kernel_alive() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="doctor",
            response_status="ok",
            data={"ready": True, "session_exists": True, "kernel_alive": True},
        )
    )

    assert suggestions == ["Kernel is already running."]


def test_advice_policy_doctor_ready_session_exists_kernel_dead() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="doctor",
            response_status="ok",
            data={"ready": True, "session_exists": True, "kernel_alive": False},
        )
    )

    assert suggestions == [
        "Session exists but kernel is not running.",
        "Run `agentnb start --json` to restart the kernel.",
    ]


def test_advice_policy_doctor_ready_without_session() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="doctor",
            response_status="ok",
            data={"ready": True},
        )
    )

    assert suggestions == ["Run `agentnb start --json` to start the kernel."]


def test_advice_policy_session_busy_suggests_wait() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="error",
            data={},
            error_code="SESSION_BUSY",
        )
    )

    assert suggestions == [
        "Run `agentnb wait --json` to block until the session is idle, then retry."
    ]


def test_advice_policy_session_busy_with_active_run_suggests_run_controls() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="error",
            data={"active_execution_id": "run-7"},
            error_code="SESSION_BUSY",
        )
    )

    assert suggestions == [
        "Run `agentnb runs wait run-7 --json` to wait for the blocking run.",
        "Run `agentnb runs show run-7 --json` to inspect the blocking run.",
    ]


def test_advice_policy_file_exec_truncation_suggests_escape_hatches() -> None:
    policy = AdvicePolicy()

    context = AdviceContext(
        command_name="exec",
        response_status="ok",
        data={
            "source_kind": "file",
            "source_path": "/tmp/project/analysis.py",
            "stdout_truncated": True,
        },
        session_id="analysis",
    )

    assert policy.suggestions(context) == [
        (
            "Run `agentnb exec --session analysis --no-truncate --file "
            "/tmp/project/analysis.py` to rerun the file without truncation."
        ),
        (
            "Run `agentnb vars --session analysis --recent 5 --json` "
            "to inspect the newest live variables."
        ),
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "command",
            "label": "Rerun without truncation",
            "command": "agentnb",
            "args": [
                "exec",
                "--session",
                "analysis",
                "--no-truncate",
                "--file",
                "/tmp/project/analysis.py",
            ],
        },
        {
            "kind": "command",
            "label": "Inspect recent vars",
            "command": "agentnb",
            "args": ["vars", "--session", "analysis", "--recent", "5", "--json"],
        },
    ]


def test_advice_policy_active_runs_follow_suggests_wait_show_cancel() -> None:
    policy = AdvicePolicy()

    context = AdviceContext(
        command_name="runs-follow",
        response_status="ok",
        data={"run": {"execution_id": "run-9", "status": "running"}},
    )

    assert policy.suggestions(context) == [
        "Run `agentnb runs wait run-9 --json` to wait for the final snapshot.",
        "Run `agentnb runs show run-9 --json` to inspect the latest run snapshot.",
        "Run `agentnb runs cancel run-9 --json` to stop the background run.",
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "command",
            "label": "Wait for run",
            "command": "agentnb",
            "args": ["runs", "wait", "run-9", "--json"],
        },
        {
            "kind": "command",
            "label": "Show run",
            "command": "agentnb",
            "args": ["runs", "show", "run-9", "--json"],
        },
        {
            "kind": "command",
            "label": "Cancel run",
            "command": "agentnb",
            "args": ["runs", "cancel", "run-9", "--json"],
        },
    ]


@pytest.mark.parametrize("error_code", ["NO_KERNEL", "BACKEND_ERROR", "KERNEL_DEAD"])
def test_advice_policy_dead_kernel_suggests_start_and_doctor(error_code: str) -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="error",
            data={},
            error_code=error_code,
        )
    )

    assert suggestions == [
        "Run `agentnb start --json` to start the kernel.",
        "Run `agentnb doctor --json` if startup has been failing.",
    ]


def test_advice_policy_reset_suggests_real_exec_command() -> None:
    policy = AdvicePolicy()
    context = AdviceContext(command_name="reset", response_status="ok", data={})

    assert policy.suggestions(context) == [
        'Run `agentnb exec "..." --json` to rebuild the state you need.'
    ]
    assert policy.suggestion_actions(context) == [
        {
            "kind": "command",
            "label": "Rebuild state",
            "command": "agentnb",
            "args": ["exec", "...", "--json"],
        }
    ]


@pytest.mark.parametrize(
    ("error_value", "expected"),
    [
        ("No module named 'pandas'", "pandas"),
        ("No module named 'sklearn.ensemble'", "sklearn"),
        ("No module named 'foo.bar.baz'", "foo"),
        (None, None),
        ("something else", None),
        ("", None),
    ],
)
def test_extract_module_name(error_value: str | None, expected: str | None) -> None:
    assert _extract_module_name(error_value) == expected
