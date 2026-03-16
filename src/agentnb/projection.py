from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from .compact import compact_run_entry, compact_traceback
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
        return payload

    def _project_agent_data(
        self,
        command_name: str,
        data: Mapping[str, object],
    ) -> dict[str, Any]:
        if command_name in {"start", "status"}:
            return _subset(
                data,
                "alive",
                "pid",
                "busy",
                "started_new",
                "waited",
                "waited_for",
            )
        if command_name in {"stop", "interrupt"}:
            return dict(data)
        if command_name == "runs-show":
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
                result = run_snapshot.get("result")
                if isinstance(result, str):
                    compacted["result_preview"] = result
                return {"run": compacted}
            return {}
        return dict(data)

    def _project_agent_error(self, response: CommandResponse) -> dict[str, Any]:
        assert response.error is not None
        payload: dict[str, Any] = {
            "code": response.error.code,
            "message": response.error.message,
        }
        if response.error.ename is not None:
            payload["ename"] = response.error.ename
        if response.error.evalue is not None:
            payload["evalue"] = response.error.evalue
        traceback = compact_traceback(response.error.traceback)
        if traceback:
            payload["traceback"] = traceback
        return payload


def _subset(data: Mapping[str, object], *keys: str) -> dict[str, Any]:
    return {key: value for key in keys if key in data for value in [data[key]] if value is not None}
