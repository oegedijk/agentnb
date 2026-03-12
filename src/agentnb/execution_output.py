from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from .contracts import ExecutionEvent

OutputItemKind = Literal["stream", "result", "display", "error", "status"]
StreamName = Literal["stdout", "stderr"]


@dataclass(slots=True)
class OutputItem:
    kind: OutputItemKind
    text: str | None = None
    stream: StreamName | None = None
    mime: dict[str, str] = field(default_factory=dict)
    ename: str | None = None
    traceback: list[str] | None = None
    state: str | None = None

    @classmethod
    def stdout(cls, text: str) -> OutputItem:
        return cls(kind="stream", text=text, stream="stdout")

    @classmethod
    def stderr(cls, text: str) -> OutputItem:
        return cls(kind="stream", text=text, stream="stderr")

    @classmethod
    def result(
        cls,
        *,
        text: str | None,
        mime: dict[str, str] | None = None,
    ) -> OutputItem:
        return cls(kind="result", text=text, mime=dict(mime or {}))

    @classmethod
    def display(
        cls,
        *,
        text: str | None,
        mime: dict[str, str] | None = None,
    ) -> OutputItem:
        return cls(kind="display", text=text, mime=dict(mime or {}))

    @classmethod
    def error(
        cls,
        *,
        ename: str | None,
        evalue: str | None,
        traceback: list[str] | None,
    ) -> OutputItem:
        return cls(
            kind="error",
            text=evalue,
            ename=ename,
            traceback=list(traceback) if traceback is not None else None,
        )

    @classmethod
    def status(cls, state: str) -> OutputItem:
        return cls(kind="status", state=state)

    @classmethod
    def from_event(cls, event: ExecutionEvent) -> OutputItem:
        if event.kind == "stdout":
            return cls.stdout(event.content or "")
        if event.kind == "stderr":
            return cls.stderr(event.content or "")
        if event.kind == "result":
            return cls.result(text=event.content, mime=_event_mime(event))
        if event.kind == "display":
            return cls.display(text=event.content, mime=_event_mime(event))
        if event.kind == "error":
            metadata = event.metadata
            traceback = metadata.get("traceback")
            return cls.error(
                ename=_optional_str(metadata.get("ename")),
                evalue=event.content,
                traceback=_optional_str_list(traceback),
            )
        if event.kind == "status" and event.content is not None:
            return cls.status(event.content)
        return cls(kind="status", state=event.content)

    def to_event(self) -> ExecutionEvent:
        if self.kind == "stream":
            event_kind = "stderr" if self.stream == "stderr" else "stdout"
            return ExecutionEvent(kind=event_kind, content=self.text)
        if self.kind == "result":
            return ExecutionEvent(
                kind="result",
                content=self.text,
                metadata=_mime_metadata(self.mime),
            )
        if self.kind == "display":
            return ExecutionEvent(
                kind="display",
                content=self.text,
                metadata=_mime_metadata(self.mime),
            )
        if self.kind == "error":
            metadata: dict[str, object] = {}
            if self.ename is not None:
                metadata["ename"] = self.ename
            if self.traceback is not None:
                metadata["traceback"] = list(self.traceback)
            return ExecutionEvent(kind="error", content=self.text, metadata=metadata)
        return ExecutionEvent(kind="status", content=self.state)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind}
        if self.text is not None:
            payload["text"] = self.text
        if self.stream is not None:
            payload["stream"] = self.stream
        if self.mime:
            payload["mime"] = dict(self.mime)
        if self.ename is not None:
            payload["ename"] = self.ename
        if self.traceback is not None:
            payload["traceback"] = list(self.traceback)
        if self.state is not None:
            payload["state"] = self.state
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> OutputItem | None:
        kind = payload.get("kind")
        if kind not in {"stream", "result", "display", "error", "status"}:
            return None
        return cls(
            kind=cast(OutputItemKind, kind),
            text=_optional_str(payload.get("text")),
            stream=_optional_stream(payload.get("stream")),
            mime=_mime_dict(payload.get("mime")),
            ename=_optional_str(payload.get("ename")),
            traceback=_optional_str_list(payload.get("traceback")),
            state=_optional_str(payload.get("state")),
        )


@dataclass(slots=True)
class ExecutionOutput:
    items: list[OutputItem] = field(default_factory=list)
    execution_count: int | None = None

    def append(self, item: OutputItem) -> None:
        self.items.append(item)

    def stdout_text(self) -> str:
        return "".join(
            item.text or ""
            for item in self.items
            if item.kind == "stream" and item.stream == "stdout"
        )

    def stderr_text(self) -> str:
        return "".join(
            item.text or ""
            for item in self.items
            if item.kind == "stream" and item.stream == "stderr"
        )

    def result_text(self) -> str | None:
        rendered: str | None = None
        for item in self.items:
            if item.kind == "result":
                rendered = item.text
            elif item.kind == "display" and item.text:
                rendered = f"{rendered}\n{item.text}" if rendered else item.text
        return rendered

    def error_item(self) -> OutputItem | None:
        for item in reversed(self.items):
            if item.kind == "error":
                return item
        return None

    def status(self) -> Literal["ok", "error"]:
        return "error" if self.error_item() is not None else "ok"

    def error_details(self) -> tuple[str | None, str | None, list[str] | None]:
        item = self.error_item()
        if item is None:
            return None, None, None
        return item.ename, item.text, item.traceback

    def to_events(self) -> list[ExecutionEvent]:
        return [item.to_event() for item in self.items]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"items": [item.to_dict() for item in self.items]}
        if self.execution_count is not None:
            payload["execution_count"] = self.execution_count
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExecutionOutput:
        raw_items = payload.get("items", [])
        items: list[OutputItem] = []
        if isinstance(raw_items, list):
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                item = OutputItem.from_dict(cast(dict[str, Any], raw_item))
                if item is not None:
                    items.append(item)
        execution_count = payload.get("execution_count")
        return cls(
            items=items,
            execution_count=execution_count if isinstance(execution_count, int) else None,
        )


def output_item_from_jupyter_message(
    msg_type: str,
    content: dict[str, object],
) -> OutputItem | None:
    if msg_type == "stream":
        name = content.get("name", "stdout")
        text = str(content.get("text", ""))
        return OutputItem.stderr(text) if name == "stderr" else OutputItem.stdout(text)

    if msg_type == "execute_result":
        mime = _mime_bundle(content)
        return OutputItem.result(text=_mime_text(mime), mime=mime)

    if msg_type == "display_data":
        mime = _mime_bundle(content)
        return OutputItem.display(text=_mime_text(mime), mime=mime)

    if msg_type == "error":
        return OutputItem.error(
            ename=_optional_str(content.get("ename")),
            evalue=_optional_str(content.get("evalue")),
            traceback=_optional_str_list(content.get("traceback")),
        )

    if msg_type == "status":
        state = _optional_str(content.get("execution_state"))
        if state is not None:
            return OutputItem.status(state)

    return None


def output_item_from_shell_reply(content: dict[str, object]) -> OutputItem | None:
    if content.get("status") != "error":
        return None
    return OutputItem.error(
        ename=_optional_str(content.get("ename")),
        evalue=_optional_str(content.get("evalue")),
        traceback=_optional_str_list(content.get("traceback")),
    )


def _mime_bundle(content: dict[str, object]) -> dict[str, str]:
    data = content.get("data")
    if not isinstance(data, dict):
        return {}
    bundle: dict[str, str] = {}
    for key, value in cast(dict[str, object], data).items():
        if isinstance(key, str) and isinstance(value, str):
            bundle[key] = value
    return bundle


def _mime_dict(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    bundle: dict[str, str] = {}
    for key, item in value.items():
        if isinstance(key, str) and isinstance(item, str):
            bundle[key] = item
    return bundle


def _mime_text(bundle: dict[str, str]) -> str | None:
    text_plain = bundle.get("text/plain")
    if text_plain is not None:
        return text_plain
    if not bundle:
        return None
    for mime_type in sorted(bundle):
        value = bundle[mime_type]
        if value:
            return value
    return None


def _event_mime(event: ExecutionEvent) -> dict[str, str]:
    mime = event.metadata.get("mime")
    if not isinstance(mime, dict):
        return {}
    bundle: dict[str, str] = {}
    for key, value in mime.items():
        if isinstance(key, str) and isinstance(value, str):
            bundle[key] = value
    return bundle


def _mime_metadata(mime: dict[str, str]) -> dict[str, str | dict[str, str]]:
    if not mime:
        return {}
    return {"mime": dict(mime)}


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_stream(value: object) -> StreamName | None:
    if value in {"stdout", "stderr"}:
        return cast(StreamName, value)
    return None


def _optional_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return [item for item in value if isinstance(item, str)]
