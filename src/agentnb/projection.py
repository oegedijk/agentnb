from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from .command_data import SerializedCommandData
from .contracts import CommandResponse
from .response_serialization import project_agent_data

ProjectionProfile = str


@dataclass(slots=True)
class ResponseProjector:
    def project(self, response: CommandResponse, *, profile: ProjectionProfile) -> dict[str, Any]:
        if profile == "full-json":
            return response.to_dict()
        if profile == "agent":
            return self._project_agent(response)
        raise ValueError(f"Unsupported projection profile: {profile}")

    def _project_agent(self, response: CommandResponse) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": response.status == "ok",
            "command": response.command,
            "session_id": response.session_id,
        }
        if response.command_data is not None:
            command_data = cast(Any, response.command_data)
        else:
            command_data = SerializedCommandData(payload=dict(response.data))
        data = project_agent_data(response.command, command_data)
        if data:
            payload["data"] = data
        if response.status == "error" and response.error is not None:
            payload["error"] = self._project_agent_error(response)
        if response.suggestion_actions:
            payload["suggestion_actions"] = list(response.suggestion_actions)
        return payload

    def _project_agent_error(self, response: CommandResponse) -> dict[str, Any]:
        assert response.error is not None
        return response.error.to_agent_dict()
