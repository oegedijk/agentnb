from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from .errors import AgentNBException
from .session import DEFAULT_SESSION_ID

ResolutionSource = Literal["explicit", "remembered", "sole_live", "default"]

if TYPE_CHECKING:
    from .runtime import RuntimeState


class SessionTargetRuntime(Protocol):
    def resolve_session_id(
        self,
        project_root: Path,
        requested_session_id: str | None,
        *,
        require_live_session: bool,
    ) -> str: ...

    def current_session_id(self, *, project_root: Path) -> str | None: ...

    def remember_current_session(self, *, project_root: Path, session_id: str) -> None: ...

    def is_live_session(self, *, project_root: Path, session_id: str) -> bool: ...

    def runtime_state(self, project_root: Path, session_id: str) -> RuntimeState: ...


@dataclass(slots=True, frozen=True)
class CommandSemantics:
    require_live_session: bool
    persist_explicit_preference: bool = False
    announce_switch: bool = False
    reject_starting_session: bool = False


@dataclass(slots=True, frozen=True)
class SessionTargetDecision:
    session_id: str
    source: ResolutionSource
    previous_preference: str | None
    updates_preference: bool = False
    switched_session: str | None = None


@dataclass(slots=True, frozen=True)
class ResolvedCommandContext:
    semantics: CommandSemantics
    decision: SessionTargetDecision
    runtime_state: RuntimeState | None = None

    @property
    def session_id(self) -> str:
        return self.decision.session_id

    @property
    def source(self) -> ResolutionSource:
        return self.decision.source

    @property
    def switched_session(self) -> str | None:
        return self.decision.switched_session


class SessionTargetingPolicy:
    def __init__(self, runtime: SessionTargetRuntime) -> None:
        self._runtime = runtime

    def resolve_command_target(
        self,
        *,
        project_root: Path,
        requested_session_id: str | None,
        require_live_session: bool,
        persist_explicit_preference: bool,
        announce_switch: bool,
    ) -> SessionTargetDecision:
        previous_preference = self.current_run_preference(project_root=project_root)
        previous_preference_live = (
            previous_preference is not None
            and self._runtime.is_live_session(
                project_root=project_root,
                session_id=previous_preference,
            )
        )
        session_id = self._runtime.resolve_session_id(
            project_root=project_root,
            requested_session_id=requested_session_id,
            require_live_session=require_live_session,
        )
        source = self._resolution_source(
            requested_session_id=requested_session_id,
            previous_preference=previous_preference,
            resolved_session_id=session_id,
        )
        updates_preference = (
            persist_explicit_preference
            and source == "explicit"
            and previous_preference != session_id
        )
        if updates_preference:
            self._runtime.remember_current_session(
                project_root=project_root,
                session_id=session_id,
            )
        switched_session = self._switched_session(
            previous_preference=previous_preference,
            previous_preference_live=previous_preference_live,
            resolved_session_id=session_id,
            source=source,
            announce_switch=announce_switch,
        )
        return SessionTargetDecision(
            session_id=session_id,
            source=source,
            previous_preference=previous_preference,
            updates_preference=updates_preference,
            switched_session=switched_session,
        )

    def current_run_preference(self, *, project_root: Path) -> str | None:
        session_id = self._runtime.current_session_id(project_root=project_root)
        if isinstance(session_id, str) and session_id:
            return session_id
        return None

    def resolve_command_context(
        self,
        *,
        project_root: Path,
        requested_session_id: str | None,
        semantics: CommandSemantics,
    ) -> ResolvedCommandContext:
        decision = self.resolve_command_target(
            project_root=project_root,
            requested_session_id=requested_session_id,
            require_live_session=semantics.require_live_session,
            persist_explicit_preference=semantics.persist_explicit_preference,
            announce_switch=semantics.announce_switch,
        )
        runtime_state = None
        if semantics.reject_starting_session:
            runtime_state = self._runtime.runtime_state(
                project_root=project_root,
                session_id=decision.session_id,
            )
            if runtime_state.kind == "starting":
                raise AgentNBException(
                    code="KERNEL_NOT_READY",
                    message="Kernel startup is still in progress or not yet ready. Wait and retry.",
                    data={
                        "session_id": decision.session_id,
                        "session_source": decision.source,
                        "runtime_state": runtime_state.kind,
                        "session_exists": runtime_state.session_exists,
                    },
                )
        return ResolvedCommandContext(
            semantics=semantics,
            decision=decision,
            runtime_state=runtime_state,
        )

    def _resolution_source(
        self,
        *,
        requested_session_id: str | None,
        previous_preference: str | None,
        resolved_session_id: str,
    ) -> ResolutionSource:
        if requested_session_id is not None:
            return "explicit"
        if previous_preference is not None and previous_preference == resolved_session_id:
            return "remembered"
        if resolved_session_id == DEFAULT_SESSION_ID:
            return "default"
        return "sole_live"

    @staticmethod
    def _switched_session(
        *,
        previous_preference: str | None,
        previous_preference_live: bool,
        resolved_session_id: str,
        source: ResolutionSource,
        announce_switch: bool,
    ) -> str | None:
        if not announce_switch:
            return None
        if previous_preference is None or previous_preference == resolved_session_id:
            return None
        if source == "remembered":
            return None
        if source != "explicit" and not previous_preference_live:
            return None
        return resolved_session_id
