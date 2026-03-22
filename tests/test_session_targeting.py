from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from agentnb.session_targeting import SessionTargetingPolicy


def test_session_targeting_persists_explicit_target(project_dir: Path) -> None:
    runtime = Mock()
    runtime.current_session_id.return_value = "default"
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
