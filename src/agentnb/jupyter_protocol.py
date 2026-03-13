from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, TypeAlias, cast

IOPubMessageType: TypeAlias = Literal[
    "execute_input",
    "stream",
    "execute_result",
    "display_data",
    "error",
    "status",
]
RichOutputMessageType: TypeAlias = Literal["execute_result", "display_data"]
ShellReplyStatus: TypeAlias = Literal["ok", "error", "abort"]


@dataclass(slots=True, frozen=True)
class ExecuteInputMessage:
    parent_id: str | None
    execution_count: int | None


@dataclass(slots=True, frozen=True)
class StreamMessage:
    parent_id: str | None
    name: str
    text: str


@dataclass(slots=True, frozen=True)
class RichOutputMessage:
    parent_id: str | None
    msg_type: RichOutputMessageType
    mime: dict[str, str]


@dataclass(slots=True, frozen=True)
class ErrorMessage:
    parent_id: str | None
    ename: str | None
    evalue: str | None
    traceback: list[str] | None


@dataclass(slots=True, frozen=True)
class StatusMessage:
    parent_id: str | None
    state: str | None


IOPubMessage: TypeAlias = (
    ExecuteInputMessage | StreamMessage | RichOutputMessage | ErrorMessage | StatusMessage
)


@dataclass(slots=True, frozen=True)
class ShellReplyMessage:
    parent_id: str | None
    status: ShellReplyStatus | str | None
    execution_count: int | None
    ename: str | None
    evalue: str | None
    traceback: list[str] | None


def message_parent_id(message: Mapping[str, object]) -> str | None:
    parent_header = message.get("parent_header")
    if not isinstance(parent_header, Mapping):
        return None
    msg_id = cast(Mapping[str, object], parent_header).get("msg_id")
    return msg_id if isinstance(msg_id, str) and msg_id else None


def message_type(message: Mapping[str, object]) -> str | None:
    value = message.get("msg_type")
    return value if isinstance(value, str) and value else None


def parse_iopub_message(message: Mapping[str, object]) -> IOPubMessage | None:
    parent_id = message_parent_id(message)
    msg_type = message_type(message)
    if msg_type is None:
        return None

    content = _message_content(message)

    if msg_type == "execute_input":
        return ExecuteInputMessage(
            parent_id=parent_id,
            execution_count=_optional_int(content.get("execution_count")),
        )

    if msg_type == "stream":
        return StreamMessage(
            parent_id=parent_id,
            name=_optional_str(content.get("name")) or "stdout",
            text=_optional_str(content.get("text")) or "",
        )

    if msg_type in {"execute_result", "display_data"}:
        return RichOutputMessage(
            parent_id=parent_id,
            msg_type=cast(RichOutputMessageType, msg_type),
            mime=_mime_bundle(content),
        )

    if msg_type == "error":
        return ErrorMessage(
            parent_id=parent_id,
            ename=_optional_str(content.get("ename")),
            evalue=_optional_str(content.get("evalue")),
            traceback=_optional_str_list(content.get("traceback")),
        )

    if msg_type == "status":
        return StatusMessage(
            parent_id=parent_id,
            state=_optional_str(content.get("execution_state")),
        )

    return None


def parse_shell_reply_message(message: Mapping[str, object]) -> ShellReplyMessage | None:
    content = _message_content(message)
    if not content:
        return None
    return ShellReplyMessage(
        parent_id=message_parent_id(message),
        status=_optional_str(content.get("status")),
        execution_count=_optional_int(content.get("execution_count")),
        ename=_optional_str(content.get("ename")),
        evalue=_optional_str(content.get("evalue")),
        traceback=_optional_str_list(content.get("traceback")),
    )


def _message_content(message: Mapping[str, object]) -> Mapping[str, object]:
    content = message.get("content")
    if isinstance(content, Mapping):
        return cast(Mapping[str, object], content)
    return {}


def _mime_bundle(content: Mapping[str, object]) -> dict[str, str]:
    data = content.get("data")
    if not isinstance(data, Mapping):
        return {}
    bundle: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(key, str) and isinstance(value, str):
            bundle[key] = value
    return bundle


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [item for item in value if isinstance(item, str)]
