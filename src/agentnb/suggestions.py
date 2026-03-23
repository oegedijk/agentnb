from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .contracts import SuggestionAction

SessionScopeSource = Literal["explicit", "remembered", "sole_live", "default"]
SuggestionOutputMode = Literal["json"]


@dataclass(slots=True, frozen=True)
class SuggestionScope:
    project_override: Path | None = None
    session_id: str | None = None
    session_source: SessionScopeSource | None = None
    output_mode: SuggestionOutputMode = "json"

    def command_args(
        self,
        *tokens: str,
        session_scoped: bool = False,
        session_id: str | None = None,
        include_output: bool = True,
    ) -> list[str]:
        args = list(tokens)
        effective_session_id = session_id
        if effective_session_id is None and session_scoped and self._preserves_session_scope():
            effective_session_id = self.session_id
        if effective_session_id:
            insertion_index = self._session_insertion_index(args)
            args[insertion_index:insertion_index] = ["--session", effective_session_id]
        if self.project_override is not None:
            args.extend(["--project", str(self.project_override)])
        if include_output and self.output_mode == "json":
            args.append("--json")
        return args

    def render_command(
        self,
        *tokens: str,
        session_scoped: bool = False,
        session_id: str | None = None,
        include_output: bool = True,
    ) -> str:
        args = self.command_args(
            *tokens,
            session_scoped=session_scoped,
            session_id=session_id,
            include_output=include_output,
        )
        return "agentnb " + " ".join(_quote_arg(arg) for arg in args)

    def command_action(
        self,
        label: str,
        *tokens: str,
        session_scoped: bool = False,
        session_id: str | None = None,
        include_output: bool = True,
    ) -> SuggestionAction:
        return {
            "kind": "command",
            "label": label,
            "command": "agentnb",
            "args": self.command_args(
                *tokens,
                session_scoped=session_scoped,
                session_id=session_id,
                include_output=include_output,
            ),
        }

    def _preserves_session_scope(self) -> bool:
        return self.session_id is not None and self.session_source in {None, "explicit"}

    @staticmethod
    def _session_insertion_index(args: list[str]) -> int:
        if len(args) >= 2 and args[0] in {"runs", "sessions"} and not args[1].startswith("-"):
            return 2
        return 1 if args else 0


def _quote_arg(arg: str) -> str:
    if arg == "...":
        return '"..."'
    return shlex.quote(arg)
