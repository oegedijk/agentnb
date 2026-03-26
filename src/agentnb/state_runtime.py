from __future__ import annotations

import json
from collections.abc import Collection
from pathlib import Path

from .state import SessionPreferences, SessionStateFiles, _runtime_artifact_session_id, _safe_unlink
from .state_layout import StateLayout
from .state_manifest import StateManifestRepository


class RuntimeStateRepository:
    def __init__(
        self,
        layout: Path | StateLayout,
        manifest_repository: StateManifestRepository | None = None,
    ) -> None:
        self.layout = layout if isinstance(layout, StateLayout) else StateLayout(layout)
        self.manifest_repository = manifest_repository or StateManifestRepository(self.layout)

    @property
    def state_dir(self):
        return self.layout.state_dir

    def ensure_initialized(self):
        return self.manifest_repository.ensure_initialized()

    def ensure_compatible(self):
        return self.manifest_repository.require_compatible()

    def ensure_gitignore_entry(self) -> bool:
        return self.layout.ensure_gitignore_entry()

    def session_runtime(self, session_id: str) -> SessionStateFiles:
        return self.layout.session_runtime(session_id)

    def session_state(self, session_id: str) -> SessionStateFiles:
        return self.layout.session_state(session_id)

    def session_files(self):
        return self.layout.session_files()

    def session_preferences(self) -> SessionPreferences:
        self.ensure_compatible()
        preferences_file = self.layout.session_preferences_file
        if not preferences_file.exists():
            return SessionPreferences()
        try:
            payload = json.loads(preferences_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _safe_unlink(preferences_file)
            return SessionPreferences()
        if not isinstance(payload, dict):
            _safe_unlink(preferences_file)
            return SessionPreferences()
        try:
            return SessionPreferences.from_dict(payload)
        except ValueError:
            _safe_unlink(preferences_file)
            return SessionPreferences()

    def save_session_preferences(self, preferences: SessionPreferences) -> None:
        self.ensure_initialized()
        self.layout.session_preferences_file.write_text(
            json.dumps(preferences.to_dict(), ensure_ascii=True),
            encoding="utf-8",
        )

    def set_current_session_id(self, session_id: str) -> None:
        self.save_session_preferences(SessionPreferences(current_session_id=session_id))

    def clear_current_session_id(self, *, expected_session_id: str | None = None) -> None:
        current = self.session_preferences().current_session_id
        if expected_session_id is not None and current != expected_session_id:
            return
        _safe_unlink(self.layout.session_preferences_file)

    def prune_session_runtime_artifacts(self, session_id: str) -> None:
        self.layout.session_runtime(session_id).clear_runtime_files()

    def prune_orphaned_runtime_artifacts(self, *, active_session_ids: Collection[str]) -> list[str]:
        active = {str(session_id) for session_id in active_session_ids}
        removed: list[str] = []
        if not self.layout.state_dir.exists():
            return removed

        for path in sorted(self.layout.state_dir.iterdir()):
            if not path.is_file():
                continue
            session_id = _runtime_artifact_session_id(path.name)
            if session_id is None or session_id in active:
                continue
            _safe_unlink(path)
            removed.append(path.name)
        return removed
