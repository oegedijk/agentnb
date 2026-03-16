from __future__ import annotations

import pytest

from agentnb.advice import AdviceContext, AdvicePolicy


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
