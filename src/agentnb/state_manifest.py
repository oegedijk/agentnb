from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .errors import StateCompatibilityError
from .state import STATE_SCHEMA_VERSION, StateManifest
from .state_layout import StateLayout


class StateManifestRepository:
    def __init__(self, layout: Path | StateLayout) -> None:
        self.layout = layout if isinstance(layout, StateLayout) else StateLayout(layout)

    def ensure_initialized(
        self,
        default_manifest: StateManifest | None = None,
        *,
        required_versions: Mapping[str, str] | None = None,
    ) -> StateManifest:
        self.layout.ensure_state_dir()
        if not self.layout.manifest_file.exists():
            manifest = default_manifest or StateManifest()
            self.save_manifest(manifest)
            return manifest
        return self.require_compatible(required_versions=required_versions)

    def manifest(self, *, required_versions: Mapping[str, str] | None = None) -> StateManifest:
        if not self.layout.manifest_file.exists():
            has_existing_state = any(
                self.layout.resource_path(name).exists()
                for name in self.layout.resources()
                if name != "legacy_session"
            )
            if has_existing_state:
                raise StateCompatibilityError(
                    "State manifest is missing for existing state.",
                    data={"state_dir": str(self.layout.state_dir)},
                )
            return StateManifest()

        try:
            payload = json.loads(self.layout.manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateCompatibilityError(
                "State manifest is unreadable.",
                data={"manifest_file": str(self.layout.manifest_file)},
            ) from exc
        if not isinstance(payload, dict):
            raise StateCompatibilityError(
                "State manifest has an invalid shape.",
                data={"manifest_file": str(self.layout.manifest_file)},
            )
        try:
            manifest = StateManifest.from_dict(payload)
        except ValueError as exc:
            raise StateCompatibilityError(
                "State manifest is missing required fields.",
                data={"manifest_file": str(self.layout.manifest_file)},
            ) from exc
        self.validate_manifest(manifest, required_versions=required_versions)
        return manifest

    def save_manifest(self, manifest: StateManifest) -> None:
        self.layout.ensure_state_dir()
        self.layout.manifest_file.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=True),
            encoding="utf-8",
        )

    def validate_manifest(
        self,
        manifest: StateManifest,
        *,
        required_versions: Mapping[str, str] | None = None,
    ) -> None:
        if manifest.schema_version != STATE_SCHEMA_VERSION:
            raise StateCompatibilityError(
                (
                    f"State schema {manifest.schema_version} is incompatible with "
                    f"agentnb schema {STATE_SCHEMA_VERSION}."
                ),
                data={
                    "manifest_schema_version": manifest.schema_version,
                    "supported_schema_version": STATE_SCHEMA_VERSION,
                },
            )

        known_resources = set(self.layout.resources())
        unknown_resources = sorted(set(manifest.resource_versions) - known_resources)
        if unknown_resources:
            raise StateCompatibilityError(
                "State manifest references unknown resources.",
                data={"unknown_resources": unknown_resources},
            )

        required = self._required_versions(required_versions)
        unsupported_resource_versions = {
            name: {
                "manifest": manifest.resource_versions.get(name),
                "supported": version,
            }
            for name, version in required.items()
            if manifest.resource_versions.get(name) != version
        }
        if unsupported_resource_versions:
            raise StateCompatibilityError(
                "State resource versions are incompatible.",
                data={"resource_versions": unsupported_resource_versions},
            )

    def require_compatible(
        self,
        *,
        required_versions: Mapping[str, str] | None = None,
    ) -> StateManifest:
        return self.manifest(required_versions=required_versions)

    @staticmethod
    def _required_versions(required_versions: Mapping[str, str] | None) -> dict[str, str]:
        if required_versions is None:
            return dict(StateManifest().resource_versions)
        return dict(required_versions)
