from __future__ import annotations

import json

import pytest

from agentnb.contracts import error_response, success_response
from agentnb.output import OutputProfile, RenderOptions, render_human, render_response


def test_render_response_json_matches_command_payload() -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={"alive": True, "pid": 123},
        suggestions=["Run `agentnb exec --json`."],
    )

    rendered = render_response(
        response,
        options=RenderOptions(profile=OutputProfile.FULL_JSON),
    )

    assert json.loads(rendered) == response.to_dict()


def test_render_human_doctor_includes_fix_hint() -> None:
    response = success_response(
        command="doctor",
        project="/tmp/project",
        session_id="default",
        data={
            "ready": False,
            "checks": [
                {
                    "name": "python",
                    "status": "ok",
                    "message": "Using interpreter: python",
                },
                {
                    "name": "ipykernel",
                    "status": "warn",
                    "message": "ipykernel is not installed.",
                    "fix_hint": "Run: python -m pip install ipykernel>=6.0",
                },
            ],
        },
    )

    rendered = render_human(response, options=RenderOptions())

    assert rendered == (
        "Doctor found issues.\n"
        "[OK] python: Using interpreter: python\n"
        "[WARN] ipykernel: ipykernel is not installed.\n"
        "  fix: Run: python -m pip install ipykernel>=6.0"
    )


def test_render_human_error_appends_traceback_and_suggestions() -> None:
    response = error_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        code="EXECUTION_ERROR",
        message="Execution failed.",
        ename="ZeroDivisionError",
        evalue="division by zero",
        traceback=["Traceback...", "ZeroDivisionError: division by zero"],
        suggestions=["Retry with a different expression."],
    )

    rendered = render_human(response, options=RenderOptions())

    assert rendered == (
        "Error: Execution failed.\n"
        "Type: ZeroDivisionError\n"
        "Detail: division by zero\n"
        "Traceback...\n"
        "ZeroDivisionError: division by zero\n\n"
        "Next:\n"
        "- Retry with a different expression."
    )


def test_render_human_runs_show_mentions_snapshot_for_running_run() -> None:
    response = success_response(
        command="runs-show",
        project="/tmp/project",
        session_id="default",
        data={
            "run": {
                "execution_id": "run-1",
                "status": "running",
                "command_type": "exec",
                "session_id": "analysis",
                "duration_ms": 12,
                "stdout": "tick 1\ntick 2\n",
                "events": [{"kind": "stdout", "content": "tick 1\n", "metadata": {}}],
            }
        },
    )

    rendered = render_human(response, options=RenderOptions())

    assert rendered == (
        "Run run-1 [running] exec on session analysis.\n"
        "duration: 12ms\n"
        "snapshot: persisted state only; use `agentnb runs follow` for live events\n"
        "stdout: tick 1 tick 2\n"
        "events: 1 recorded"
    )


def test_render_human_quiet_suppresses_status_body() -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={"alive": True, "pid": 123},
        suggestions=["This should be suppressed."],
    )

    rendered = render_human(
        response,
        options=RenderOptions(quiet_human=True, suppress_suggestions=True),
    )

    assert rendered == ""


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"alive": True, "pid": 123, "started_new": True, "python": "python3.12"},
            "Kernel started (pid 123) using python3.12.",
        ),
        (
            {"alive": True, "pid": 123, "started_new": False, "python": None},
            "Kernel already running (pid 123).",
        ),
        (
            {"alive": False},
            "Kernel is not running.",
        ),
    ],
)
def test_render_human_start_variants(data: dict[str, object], expected: str) -> None:
    response = success_response(
        command="start",
        project="/tmp/project",
        session_id="default",
        data=data,
    )

    assert render_human(response, options=RenderOptions()) == expected


def test_render_human_status_busy_and_interrupt_stop() -> None:
    status_response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={"alive": True, "pid": 321, "busy": True},
    )
    stop_response = success_response(
        command="stop",
        project="/tmp/project",
        session_id="default",
    )
    interrupt_response = success_response(
        command="interrupt",
        project="/tmp/project",
        session_id="default",
    )

    assert (
        render_human(
            status_response,
            options=RenderOptions(),
        )
        == "Kernel is running (session: default, pid 321, busy)."
    )
    assert render_human(stop_response, options=RenderOptions()) == "Kernel stopped."
    assert render_human(interrupt_response, options=RenderOptions()) == "Interrupt signal sent."


def test_render_human_status_busy_includes_busy_duration() -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={"alive": True, "pid": 321, "busy": True, "busy_for_ms": 1500},
    )

    assert (
        render_human(response, options=RenderOptions())
        == "Kernel is running (session: default, pid 321, busy for 1.5s)."
    )


@pytest.mark.parametrize(
    ("command", "data", "expected"),
    [
        (
            "status",
            {"alive": False, "runtime_state": "starting"},
            "Kernel is starting (session: default).",
        ),
        (
            "status",
            {"alive": False, "runtime_state": "dead"},
            "Kernel is dead (session: default).",
        ),
        (
            "wait",
            {"alive": False, "runtime_state": "starting"},
            "Kernel is starting (session: default).",
        ),
    ],
)
def test_render_human_projects_runtime_state(
    command: str,
    data: dict[str, object],
    expected: str,
) -> None:
    response = success_response(
        command=command,
        project="/tmp/project",
        session_id="default",
        data=data,
    )

    assert render_human(response, options=RenderOptions()) == expected


def test_render_human_wait_variants() -> None:
    idle_response = success_response(
        command="wait",
        project="/tmp/project",
        session_id="default",
        data={"alive": True, "pid": 321, "busy": False, "waited": False},
    )
    ready_response = success_response(
        command="wait",
        project="/tmp/project",
        session_id="default",
        data={"alive": True, "pid": 654, "busy": False, "waited": True, "waited_for": "ready"},
    )

    assert (
        render_human(idle_response, options=RenderOptions())
        == "Kernel is idle (session: default, pid 321)."
    )
    assert (
        render_human(ready_response, options=RenderOptions())
        == "Kernel is ready (session: default, pid 654)."
    )


def test_render_human_status_wait_includes_wait_detail() -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={
            "alive": True,
            "pid": 321,
            "busy": False,
            "waited": True,
            "waited_for": "idle",
            "waited_ms": 3900,
            "initial_runtime_state": "busy",
        },
    )

    assert (
        render_human(response, options=RenderOptions())
        == "Kernel is running (session: default, pid 321, waited 3.9s for idle from busy)."
    )


def test_render_human_exec_renders_stdout_stderr_and_result() -> None:
    response = success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data={"stdout": "hello\n", "stderr": "warn\n", "result": "2"},
    )

    assert render_human(response, options=RenderOptions()) == "hello\n[stderr]\nwarn\n2"


def test_render_human_exec_selected_output_returns_selected_text_only() -> None:
    response = success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data={
            "selected_output": "stdout",
            "selected_text": "exact output\n",
        },
    )

    assert render_human(response, options=RenderOptions()) == "exact output"


def test_render_human_exec_without_output_reports_completion() -> None:
    exec_response = success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data={},
    )
    reset_response = success_response(
        command="reset",
        project="/tmp/project",
        session_id="default",
        data={},
    )

    assert render_human(exec_response, options=RenderOptions()) == "Execution completed."
    assert render_human(reset_response, options=RenderOptions()) == "Namespace cleared."


def test_render_human_status_includes_session_name() -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="analysis",
        data={"alive": True, "pid": 999, "busy": False},
    )

    body = render_human(response, options=RenderOptions())

    assert "session: analysis" in body
    assert "pid 999" in body


def test_render_human_wait_includes_session_name() -> None:
    response = success_response(
        command="wait",
        project="/tmp/project",
        session_id="analysis",
        data={"alive": True, "pid": 999, "waited_for": "idle"},
    )

    body = render_human(response, options=RenderOptions())

    assert "session: analysis" in body
    assert "pid 999" in body


def test_render_human_vars_and_empty_history() -> None:
    vars_response = success_response(
        command="vars",
        project="/tmp/project",
        session_id="default",
        data={"vars": [{"name": "value", "repr": "42", "type": "int"}]},
    )
    history_response = success_response(
        command="history",
        project="/tmp/project",
        session_id="default",
        data={"entries": []},
    )

    assert (
        render_human(vars_response, options=RenderOptions()) == "session: default\nvalue: 42 (int)"
    )
    assert render_human(history_response, options=RenderOptions()) == "No history entries."


def test_render_human_vars_appends_helper_access_note() -> None:
    response = success_response(
        command="vars",
        project="/tmp/project",
        session_id="default",
        data={
            "vars": [{"name": "value", "repr": "42", "type": "int"}],
            "started_new_session": True,
            "waited": True,
            "waited_for": "idle",
            "waited_ms": 25,
            "initial_runtime_state": "busy",
        },
    )

    assert render_human(response, options=RenderOptions()) == (
        "session: default\nvalue: 42 (int)\n(auto-started session; waited 25ms for idle from busy)"
    )


def test_render_human_inspect_generic_shape() -> None:
    response = success_response(
        command="inspect",
        project="/tmp/project",
        session_id="default",
        data={
            "inspect": {
                "name": "thing",
                "type": "Thing",
                "repr": "Thing(value=1)",
                "members": ["alpha", "beta"],
            }
        },
    )

    assert render_human(response, options=RenderOptions()) == (
        "session: default\nname: thing\ntype: Thing\nrepr: Thing(value=1)\nmembers: alpha, beta"
    )


def test_render_human_inspect_mapping_preview() -> None:
    response = success_response(
        command="inspect",
        project="/tmp/project",
        session_id="default",
        data={
            "inspect": {
                "name": "payload",
                "type": "dict",
                "preview": {
                    "kind": "mapping-like",
                    "length": 2,
                    "keys": ["alpha", "beta"],
                    "sample": {"alpha": 1, "beta": 2},
                },
            }
        },
    )

    assert render_human(response, options=RenderOptions()) == (
        "session: default\nname: payload\ntype: dict\nlength: 2\nkeys: alpha, beta\n"
        'sample: {"alpha": 1, "beta": 2}'
    )


def test_render_human_history_formats_internal_and_exec_fallback_labels() -> None:
    response = success_response(
        command="history",
        project="/tmp/project",
        session_id="default",
        data={
            "entries": [
                {
                    "ts": "2026-03-11T00:00:00+00:00",
                    "status": "ok",
                    "duration_ms": 5,
                    "kind": "kernel_execution",
                    "command_type": "reload",
                    "code": "import localmod",
                },
                {
                    "ts": "2026-03-11T00:00:01+00:00",
                    "status": "ok",
                    "duration_ms": 6,
                    "kind": "user_command",
                    "command_type": "exec",
                    "input": "value = 1\nvalue",
                },
            ]
        },
    )

    assert render_human(response, options=RenderOptions()) == (
        "2026-03-11T00:00:00+00:00 [ok] 5ms [internal] import localmod\n"
        "2026-03-11T00:00:01+00:00 [ok] 6ms exec value = 1 value"
    )


def test_render_human_sessions_views() -> None:
    list_response = success_response(
        command="sessions-list",
        project="/tmp/project",
        session_id="default",
        data={
            "sessions": [
                {
                    "session_id": "default",
                    "pid": 11,
                    "python": "python",
                    "is_default": True,
                    "is_current": False,
                },
                {
                    "session_id": "analysis",
                    "pid": 22,
                    "python": None,
                    "is_default": False,
                    "is_current": True,
                },
            ]
        },
    )
    empty_response = success_response(
        command="sessions-list",
        project="/tmp/project",
        session_id="default",
        data={"sessions": []},
    )
    delete_response = success_response(
        command="sessions-delete",
        project="/tmp/project",
        session_id="analysis",
        data={"session_id": "analysis", "stopped_running_kernel": True},
    )

    assert render_human(list_response, options=RenderOptions()) == (
        "default (default): pid 11 using python\nanalysis (current): pid 22"
    )
    assert render_human(empty_response, options=RenderOptions()) == "No sessions found."
    assert (
        render_human(delete_response, options=RenderOptions())
        == "Deleted session analysis and stopped its kernel."
    )


def test_render_human_runs_list_and_wait_error_shape() -> None:
    list_response = success_response(
        command="runs-list",
        project="/tmp/project",
        session_id="default",
        data={
            "runs": [
                {
                    "ts": "2026-03-11T00:00:00+00:00",
                    "status": "ok",
                    "execution_id": "run-1",
                    "command_type": "exec",
                    "duration_ms": 9,
                }
            ]
        },
    )
    wait_response = success_response(
        command="runs-wait",
        project="/tmp/project",
        session_id="default",
        data={
            "run": {
                "execution_id": "run-2",
                "status": "error",
                "command_type": "exec",
                "session_id": "default",
                "duration_ms": 12,
                "stderr": "warning",
                "result": "partial",
                "ename": "RuntimeError",
                "evalue": "boom",
                "events": [],
            }
        },
    )

    assert render_human(list_response, options=RenderOptions()) == (
        "2026-03-11T00:00:00+00:00 [ok] run-1 exec 9ms"
    )
    assert render_human(wait_response, options=RenderOptions()) == (
        "Run run-2 [error] exec on session default.\n"
        "duration: 12ms\n"
        "stderr: warning\n"
        "result: partial\n"
        "error: RuntimeError: boom\n"
        "events: 0 recorded"
    )


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (
            {"execution_id": "run-1", "cancel_requested": True, "session_outcome": "preserved"},
            "Cancelled run run-1. The session was preserved.",
        ),
        (
            {"execution_id": "run-1", "cancel_requested": True, "session_outcome": "stopped"},
            "Cancelled run run-1. The still-starting session was stopped.",
        ),
        (
            {"execution_id": "run-1", "cancel_requested": True, "session_outcome": "unchanged"},
            "Cancel requested for run run-1.",
        ),
        (
            {"execution_id": "run-1", "cancel_requested": True, "status": "ok"},
            "Cancel requested for run run-1, but it completed before cancellation took effect.",
        ),
        (
            {"execution_id": "run-1", "cancel_requested": False, "status": "ok"},
            "Run run-1 already finished.",
        ),
        (
            {"execution_id": "run-1", "cancel_requested": False, "status": "error"},
            "Run run-1 already failed.",
        ),
        (
            {"execution_id": "run-1", "cancel_requested": False, "status": "cancelled"},
            "Run run-1 already cancelled.",
        ),
    ],
)
def test_render_human_runs_cancel_variants(
    data: dict[str, object],
    expected: str,
) -> None:
    response = success_response(
        command="runs-cancel",
        project="/tmp/project",
        session_id="default",
        data=data,
    )

    assert render_human(response, options=RenderOptions()) == expected


def test_render_human_reload_variants_and_unknown_command() -> None:
    reload_response = success_response(
        command="reload",
        project="/tmp/project",
        session_id="default",
        data={
            "requested_module": "localmod",
            "reloaded_modules": ["localmod"],
            "rebound_names": ["greet"],
            "stale_names": ["instance"],
            "failed_modules": [{"module": "broken_mod"}],
            "notes": ["Reload note"],
        },
    )
    unknown_response = success_response(
        command="custom",
        project="/tmp/project",
        session_id="default",
        data={"alpha": 1},
    )

    assert render_human(
        reload_response,
        options=RenderOptions(suppress_suggestions=True),
    ) == (
        "Reloaded module: localmod\n"
        "Rebound names: greet\n"
        "Possible stale objects: instance\n"
        "Recreate them or run `agentnb reset` if stale state is widespread.\n"
        "Failed modules: broken_mod\n"
        "Reload note"
    )
    assert render_human(unknown_response, options=RenderOptions()) == '{\n  "alpha": 1\n}'


def test_render_options_resolve_agent_profile() -> None:
    options = RenderOptions.resolve(
        root_as_json=False,
        agent=True,
        quiet=False,
        no_suggestions=False,
        env={},
    )

    assert options.profile == OutputProfile.AGENT
    assert options.as_json is True
    assert options.quiet is True
    assert options.show_suggestions is False


def test_render_response_agent_uses_compact_projection() -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={"alive": True, "pid": 123, "busy": False},
        suggestions=["This should not appear in the agent payload."],
    )

    rendered = render_response(
        response,
        options=RenderOptions(profile=OutputProfile.AGENT),
    )

    assert json.loads(rendered) == {
        "ok": True,
        "command": "status",
        "session_id": "default",
        "data": {"alive": True, "pid": 123, "busy": False},
    }


def test_render_human_exec_background_dispatch_message() -> None:
    response = success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data={"background": True, "execution_id": "run-42"},
    )

    assert render_human(response, options=RenderOptions()) == (
        "Background execution started (run-42)."
    )


def test_render_human_session_switch_note() -> None:
    response = success_response(
        command="exec",
        project="/tmp/project",
        session_id="analysis",
        data={"stdout": "2\n", "switched_session": "analysis"},
    )

    assert render_human(response, options=RenderOptions()) == "2\n(now targeting session: analysis)"
