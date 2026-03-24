from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from agentnb.contracts import KernelStatus
from agentnb.errors import AgentNBException
from agentnb.runtime import RuntimeState
from agentnb.session_targeting import CommandSemantics, SessionTargetingPolicy


def test_session_targeting_persists_explicit_target(project_dir: Path) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "default"
    runtime.is_live_session.return_value = True
    runtime.resolve_session_id.return_value = "analysis"
    policy = SessionTargetingPolicy(runtime)

    decision = policy.resolve_command_target(
        project_root=project_dir,
        requested_session_id="analysis",
        require_live_session=True,
        persist_explicit_preference=True,
        announce_switch=True,
    )

    assert decision.session_id == "analysis"
    assert decision.source == "explicit"
    assert decision.updates_preference is True
    assert decision.switched_session == "analysis"
    runtime.remember_current_session.assert_called_once_with(
        project_root=project_dir,
        session_id="analysis",
    )


def test_session_targeting_does_not_persist_implicit_target(project_dir: Path) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "default"
    runtime.is_live_session.return_value = True
    runtime.resolve_session_id.return_value = "analysis"
    policy = SessionTargetingPolicy(runtime)

    decision = policy.resolve_command_target(
        project_root=project_dir,
        requested_session_id=None,
        require_live_session=True,
        persist_explicit_preference=True,
        announce_switch=True,
    )

    assert decision.session_id == "analysis"
    assert decision.source == "sole_live"
    assert decision.updates_preference is False
    assert decision.switched_session == "analysis"
    runtime.remember_current_session.assert_not_called()


def test_session_targeting_can_skip_persisting_explicit_target(project_dir: Path) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "default"
    runtime.is_live_session.return_value = True
    runtime.resolve_session_id.return_value = "analysis"
    policy = SessionTargetingPolicy(runtime)

    decision = policy.resolve_command_target(
        project_root=project_dir,
        requested_session_id="analysis",
        require_live_session=False,
        persist_explicit_preference=False,
        announce_switch=False,
    )

    assert decision.session_id == "analysis"
    assert decision.source == "explicit"
    assert decision.updates_preference is False
    assert decision.switched_session is None
    runtime.remember_current_session.assert_not_called()


def test_session_targeting_uses_current_preference_for_run_scope(project_dir: Path) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "analysis"
    policy = SessionTargetingPolicy(runtime)

    preference = policy.current_run_preference(project_root=project_dir)

    assert preference == "analysis"


def test_session_targeting_suppresses_implicit_switch_notice_for_non_live_preference(
    project_dir: Path,
) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "default"
    runtime.is_live_session.return_value = False
    runtime.resolve_session_id.return_value = "analysis"
    policy = SessionTargetingPolicy(runtime)

    decision = policy.resolve_command_target(
        project_root=project_dir,
        requested_session_id=None,
        require_live_session=True,
        persist_explicit_preference=True,
        announce_switch=True,
    )

    assert decision.session_id == "analysis"
    assert decision.source == "sole_live"
    assert decision.updates_preference is False
    assert decision.switched_session is None


def test_session_targeting_resolve_command_context_rejects_starting_session(
    project_dir: Path,
) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "default"
    runtime.is_live_session.return_value = True
    runtime.resolve_session_id.return_value = "analysis"
    runtime.runtime_state.return_value = RuntimeState(
        kind="starting",
        session_id="analysis",
        kernel_status=KernelStatus(alive=False),
        has_connection_file=True,
    )
    policy = SessionTargetingPolicy(runtime)

    with pytest.raises(AgentNBException) as exc_info:
        policy.resolve_command_context(
            project_root=project_dir,
            requested_session_id=None,
            semantics=CommandSemantics(
                require_live_session=True,
                reject_starting_session=True,
            ),
        )

    assert exc_info.value.code == "KERNEL_NOT_READY"
    assert exc_info.value.data["session_id"] == "analysis"
    assert exc_info.value.data["runtime_state"] == "starting"


def test_session_targeting_resolve_command_context_returns_runtime_state_when_checked(
    project_dir: Path,
) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "default"
    runtime.is_live_session.return_value = True
    runtime.resolve_session_id.return_value = "analysis"
    runtime.runtime_state.return_value = RuntimeState(
        kind="ready",
        session_id="analysis",
        kernel_status=KernelStatus(alive=True, pid=123, busy=False),
    )
    policy = SessionTargetingPolicy(runtime)

    context = policy.resolve_command_context(
        project_root=project_dir,
        requested_session_id=None,
        semantics=CommandSemantics(
            require_live_session=True,
            reject_starting_session=True,
        ),
    )

    assert context.session_id == "analysis"
    assert context.runtime_state is not None
    assert context.runtime_state.kind == "ready"
