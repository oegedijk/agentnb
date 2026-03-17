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
        "Install the missing module: `pip install pandas` or `uv add pandas`.",
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
        "Install the missing module: `pip install sklearn` or `uv add sklearn`.",
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


def test_advice_policy_doctor_ready_with_session_exists() -> None:
    policy = AdvicePolicy()

    suggestions = policy.suggestions(
        AdviceContext(
            command_name="doctor",
            response_status="ok",
            data={"ready": True, "session_exists": True},
        )
    )

    assert suggestions == ["Kernel is already running."]


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
