from __future__ import annotations

import json as _json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .compact import compact_run_entry
from .contracts import CommandResponse
from .payloads import RunSnapshot

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
        data = self._project_agent_data(response.command, response.data)
        if data:
            payload["data"] = data
        if response.status == "error" and response.error is not None:
            payload["error"] = self._project_agent_error(response)
        if response.suggestion_actions:
            payload["suggestion_actions"] = list(response.suggestion_actions)
        return payload

    def _project_agent_data(
        self,
        command_name: str,
        data: Mapping[str, object],
    ) -> dict[str, Any]:
        if command_name in {"start", "status", "wait"}:
            return _subset(
                data,
                "alive",
                "pid",
                "busy",
                "lock_pid",
                "lock_acquired_at",
                "busy_for_ms",
                "runtime_state",
                "started_new",
                "waited",
                "waited_for",
                "waited_ms",
                "initial_runtime_state",
            )
        if command_name in {"stop", "interrupt"}:
            return dict(data)
        if command_name in {"exec", "reset"}:
            compacted = _subset(
                data,
                "status",
                "execution_id",
                "duration_ms",
                "background",
                "ensured_started",
                "started_new_session",
                "wait_behavior",
                "waited_ms",
                "lock_pid",
                "lock_acquired_at",
                "busy_for_ms",
                "active_execution_id",
            )
            for key in ("result", "stdout", "stderr", "selected_output", "selected_text"):
                value = data.get(key)
                if isinstance(value, str) and value:
                    compacted[key] = value
            result_preview = data.get("result_preview")
            if isinstance(result_preview, dict):
                compacted["result_preview"] = dict(result_preview)
            result = compacted.get("result")
            if isinstance(result, str):
                parsed = _try_parse_result_json(result)
                if parsed is not _SENTINEL:
                    compacted["result_json"] = parsed
            return compacted
        if command_name in {"history", "runs-list", "sessions-list"}:
            return dict(data)
        if command_name == "runs-show":
            run = data.get("run")
            if isinstance(run, dict):
                return {"run": compact_run_entry(cast(RunSnapshot, run))}
            return {}
        if command_name == "runs-follow":
            run = data.get("run")
            if isinstance(run, dict):
                return {"run": compact_run_entry(cast(RunSnapshot, run))}
            return {}
        if command_name == "runs-wait":
            run = data.get("run")
            if isinstance(run, dict):
                run_snapshot = cast(RunSnapshot, run)
                compacted = compact_run_entry(run_snapshot)
                compacted["status"] = run_snapshot.get("status")
                return {"run": compacted}
            return {}
        if command_name == "runs-cancel":
            return _subset(
                data,
                "execution_id",
                "session_id",
                "cancel_requested",
                "status",
                "run_status",
                "session_outcome",
            )
        return dict(data)

    def _project_agent_error(self, response: CommandResponse) -> dict[str, Any]:
        assert response.error is not None
        return response.error.to_agent_dict()


_SENTINEL = object()


def _try_parse_result_json(result: str) -> Any:
    """Attempt to extract a JSON value from a Python repr result string.

    Handles: plain JSON literals ("42", "true", "[1,2]", '{"a":1}'),
    and Python repr of strings that contain JSON ("'{...}'" or '"[...]"').
    """
    try:
        return _json.loads(result)
    except (ValueError, _json.JSONDecodeError):
        pass
    if len(result) >= 2 and result[0] in ("'", '"') and result[-1] == result[0]:
        inner = result[1:-1]
        try:
            return _json.loads(inner)
        except (ValueError, _json.JSONDecodeError):
            pass
    return _SENTINEL


def _subset(data: Mapping[str, object], *keys: str) -> dict[str, Any]:
    return {key: value for key in keys if key in data for value in [data[key]] if value is not None}
