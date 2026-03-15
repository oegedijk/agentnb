from __future__ import annotations

import pytest

from agentnb.contracts import ExecutionEvent
from agentnb.execution_invocation import ExecInvocationPolicy


class DummySink:
    def started(self, *, execution_id: str, session_id: str) -> None:
        del execution_id, session_id

    def accept(self, event: ExecutionEvent) -> None:
        del event


def test_exec_invocation_policy_from_cli_preserves_flag_state() -> None:
    policy = ExecInvocationPolicy.from_cli(
        ensure_started=True,
        background=False,
        stream=True,
        output_selector=None,
    )

    assert policy.ensure_started is True
    assert policy.is_background is False
    assert policy.is_stream is True
    assert policy.output_selector is None


@pytest.mark.parametrize(
    ("policy", "expected_message"),
    [
        (
            ExecInvocationPolicy(background=True, output_selector="stdout"),
            "Output selectors are not supported with --background.",
        ),
        (
            ExecInvocationPolicy(background=True, stream=True),
            "--stream and --background cannot be used together.",
        ),
        (
            ExecInvocationPolicy(stream=True, output_selector="result"),
            "Output selectors are not supported with --stream.",
        ),
    ],
)
def test_exec_invocation_policy_reports_invalid_cli_combinations(
    policy: ExecInvocationPolicy,
    expected_message: str,
) -> None:
    assert policy.validation_error() == expected_message


def test_exec_invocation_policy_streaming_sink_only_for_streaming_mode() -> None:
    sink = DummySink()

    assert ExecInvocationPolicy(stream=True).streaming_sink(sink) is sink
    assert ExecInvocationPolicy().streaming_sink(sink) is None
