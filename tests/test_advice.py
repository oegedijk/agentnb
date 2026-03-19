from __future__ import annotations

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
            ["Run `agentnb wait --json` to wait until the session is ready."],
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
                ("Run `agentnb runs show EXECUTION_ID --json` to inspect the current run record."),
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

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="status",
            response_status="error",
            data={},
            error_code="AMBIGUOUS_SESSION",
        )
    )

    assert suggestions == [
        "Run `agentnb sessions list --json` to see the live session names.",
        "Retry with `agentnb status --session NAME --json` to target one explicitly.",
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


def test_advice_policy_module_not_found_error_suggests_install() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="exec",
            response_status="error",
            data={},
            error_code="EXECUTION_ERROR",
            error_name="ModuleNotFoundError",
            error_value="No module named 'pandas'",
        )
    )

    assert suggestions == [
        "Install the missing module: run `uv add pandas` in your shell (not inside the session).",
        "Then retry the execution.",
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
        "Install the missing module: run `uv add sklearn` in your shell (not inside the session).",
        "Then retry the execution.",
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
        "Run `agentnb history @last-error --json` to review the latest failure.",
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


def test_advice_policy_status_starting_suggests_wait() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="status",
            response_status="ok",
            data={"alive": False, "runtime_state": "starting"},
        )
    )

    assert suggestions == ["Run `agentnb wait --json` to wait for startup to finish."]


def test_advice_policy_kernel_not_ready_suggests_wait_and_status() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="vars",
            response_status="error",
            data={"runtime_state": "starting"},
            error_code="KERNEL_NOT_READY",
        )
    )

    assert suggestions == [
        "Run `agentnb wait --json` to wait for startup to finish.",
        "Run `agentnb status --json` to inspect the current session state.",
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
