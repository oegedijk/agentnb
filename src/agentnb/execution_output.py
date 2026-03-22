from __future__ import annotations

import ast
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Literal, cast

from .contracts import ExecutionEvent
from .kernel.jupyter_protocol import (
    ErrorMessage,
    ExecuteInputMessage,
    IOPubMessage,
    RichOutputMessage,
    ShellReplyMessage,
    StatusMessage,
    StreamMessage,
)
from .payloads import DataframePreview, InspectPreview, JSONValue, MappingPreview, SequencePreview

OutputItemKind = Literal["stream", "result", "display", "error", "status"]
StreamName = Literal["stdout", "stderr"]
_DATAFRAME_SHAPE_RE = re.compile(r"\[(\d+)\s+rows\s+x\s+(\d+)\s+columns\]\s*$")


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

    def result_item(self) -> OutputItem | None:
        for item in reversed(self.items):
            if item.kind == "result":
                return item
        return None

    def result_preview(self) -> InspectPreview | None:
        item = self.result_item()
        if item is None:
            return None
        preview = _preview_from_mime(item.mime)
        if preview is not None:
            return preview
        return _preview_from_text(item.text)

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

    def refined_with_error(self, error: OutputItem | None) -> ExecutionOutput:
        if error is None or error.kind != "error":
            return ExecutionOutput(
                items=list(self.items),
                execution_count=self.execution_count,
            )

        items = list(self.items)
        for index in range(len(items) - 1, -1, -1):
            if items[index].kind == "error":
                items[index] = error
                break

        return ExecutionOutput(items=items, execution_count=self.execution_count)

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


@dataclass(slots=True, frozen=True)
class CompatibilityOutput:
    status: Literal["ok", "error"]
    stdout: str
    stderr: str
    result: str | None
    ename: str | None
    evalue: str | None
    traceback: list[str] | None


def compatibility_output(output: ExecutionOutput) -> CompatibilityOutput:
    ename, evalue, traceback = output.error_details()
    return CompatibilityOutput(
        status=output.status(),
        stdout=output.stdout_text(),
        stderr=output.stderr_text(),
        result=output.result_text(),
        ename=ename,
        evalue=evalue,
        traceback=traceback,
    )


def execution_output_from_events(
    events: list[ExecutionEvent],
    *,
    execution_count: int | None = None,
) -> ExecutionOutput:
    return ExecutionOutput(
        items=[OutputItem.from_event(event) for event in events],
        execution_count=execution_count,
    )


def execution_output_from_legacy_fields(
    *,
    stdout: str = "",
    stderr: str = "",
    result: str | None = None,
    ename: str | None = None,
    evalue: str | None = None,
    traceback: list[str] | None = None,
    status: Literal["ok", "error"] = "ok",
    execution_count: int | None = None,
) -> ExecutionOutput:
    items: list[OutputItem] = []
    if stdout:
        items.append(OutputItem.stdout(stdout))
    if stderr:
        items.append(OutputItem.stderr(stderr))
    if result is not None:
        mime = {"text/plain": result} if result else {}
        items.append(OutputItem.result(text=result, mime=mime))
    del status, ename, evalue, traceback
    return ExecutionOutput(items=items, execution_count=execution_count)


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


def output_item_from_iopub_message(message: IOPubMessage) -> OutputItem | None:
    if isinstance(message, ExecuteInputMessage):
        return None
    if isinstance(message, StreamMessage):
        if message.name == "stderr":
            return OutputItem.stderr(message.text)
        return OutputItem.stdout(message.text)
    if isinstance(message, RichOutputMessage):
        text = _mime_text(message.mime)
        if message.msg_type == "display_data":
            return OutputItem.display(text=text, mime=message.mime)
        return OutputItem.result(text=text, mime=message.mime)
    if isinstance(message, ErrorMessage):
        return OutputItem.error(
            ename=message.ename,
            evalue=message.evalue,
            traceback=message.traceback,
        )
    if isinstance(message, StatusMessage) and message.state is not None:
        return OutputItem.status(message.state)
    return None


def output_item_from_shell_reply(content: dict[str, object]) -> OutputItem | None:
    if content.get("status") != "error":
        return None
    return OutputItem.error(
        ename=_optional_str(content.get("ename")),
        evalue=_optional_str(content.get("evalue")),
        traceback=_optional_str_list(content.get("traceback")),
    )


def output_item_from_shell_reply_message(message: ShellReplyMessage) -> OutputItem | None:
    if message.status != "error":
        return None
    return OutputItem.error(
        ename=message.ename,
        evalue=message.evalue,
        traceback=message.traceback,
    )


def _preview_from_mime(bundle: dict[str, str]) -> InspectPreview | None:
    json_payload = bundle.get("application/json")
    if isinstance(json_payload, str):
        try:
            decoded = json.loads(json_payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            decoded = None
        preview = _preview_from_value(decoded)
        if preview is not None:
            return preview

    text_plain = bundle.get("text/plain")
    html = bundle.get("text/html")
    if isinstance(html, str) and "dataframe" in html.lower():
        preview = _dataframe_preview_from_bundle(text_plain=text_plain, html=html)
        if preview is not None:
            return preview
    return _preview_from_text(text_plain)


def _preview_from_text(text: str | None) -> InspectPreview | None:
    if not isinstance(text, str) or not text.strip():
        return None
    dataframe_preview = _dataframe_preview_from_bundle(text_plain=text, html=None)
    if dataframe_preview is not None:
        return dataframe_preview
    try:
        value = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        try:
            value = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
    return _preview_from_value(value)


def preview_from_result_text(text: str | None) -> InspectPreview | None:
    return _preview_from_text(text)


def _preview_from_value(value: object) -> InspectPreview | None:
    if isinstance(value, dict):
        return _mapping_preview_from_value(cast(Mapping[object, object], value))
    if isinstance(value, (list, tuple, set)):
        return _sequence_preview_from_value(list(value))
    return None


def _mapping_preview_from_value(value: Mapping[object, object]) -> MappingPreview:
    keys = [str(key) for key in list(value)[:10]]
    sample: dict[str, JSONValue] = {}
    for index, (key, item) in enumerate(value.items()):
        if index >= 3:
            break
        sample[str(key)] = _json_safe(item)
    return {
        "kind": "mapping-like",
        "length": len(value),
        "keys": keys,
        "sample": sample,
    }


def _sequence_preview_from_value(value: list[object]) -> SequencePreview:
    preview: SequencePreview = {
        "kind": "sequence-like",
        "length": len(value),
        "sample": [_json_safe(item) for item in value[:3]],
    }
    if value:
        preview["item_type"] = type(value[0]).__name__
        if isinstance(value[0], dict):
            preview["sample_keys"] = [str(key) for key in list(value[0])[:10]]
    return preview


def _dataframe_preview_from_bundle(
    *,
    text_plain: str | None,
    html: str | None,
) -> DataframePreview | None:
    shape = _dataframe_shape(text_plain)
    headers: list[str] = []
    head_rows: list[dict[str, JSONValue]] = []
    if isinstance(html, str) and html:
        headers, head_rows = _parse_html_table_preview(html)
    if not headers and isinstance(text_plain, str):
        headers = _dataframe_columns_from_text(text_plain)

    if shape is None and not headers and not head_rows:
        return None
    if not isinstance(html, str):
        if shape is None and (not isinstance(text_plain, str) or "\n" not in text_plain):
            return None
        if shape is None and len(headers) < 2:
            return None

    preview: DataframePreview = {"kind": "dataframe-like"}
    if shape is not None:
        preview["shape"] = [shape[0], shape[1]]
    if headers:
        preview["columns"] = headers
        preview["column_count"] = len(headers)
    if head_rows:
        preview["head"] = head_rows
    return preview


def _dataframe_shape(text: str | None) -> tuple[int, int] | None:
    if not isinstance(text, str):
        return None
    match = _DATAFRAME_SHAPE_RE.search(text.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _dataframe_columns_from_text(text: str) -> list[str]:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("[") or stripped.startswith(".."):
            continue
        parts = [part for part in re.split(r"\s{2,}", stripped) if part]
        if len(parts) >= 2 and parts[0].isdigit():
            continue
        if parts:
            return parts
    return []


def _parse_html_table_preview(html: str) -> tuple[list[str], list[dict[str, JSONValue]]]:
    parser = _HTMLTablePreviewParser()
    parser.feed(html)
    parser.close()
    return parser.columns(), parser.rows()


def _json_safe(value: object, depth: int = 0) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= 80 else value[:77] + "..."
    if depth >= 2:
        text = str(value)
        return text if len(text) <= 80 else text[:77] + "..."
    if isinstance(value, dict):
        sample: dict[str, JSONValue] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 5:
                break
            sample[str(key)] = _json_safe(item, depth + 1)
        return sample
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth + 1) for item in list(value)[:3]]
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


class _HTMLTablePreviewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_table = False
        self._in_head = False
        self._in_body = False
        self._in_row = False
        self._in_cell = False
        self._current_cell: list[str] = []
        self._current_row: list[str] = []
        self._header_rows: list[list[str]] = []
        self._body_rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table" and not self._in_table:
            attrs_dict = dict(attrs)
            css_class = attrs_dict.get("class", "") or ""
            if "dataframe" in css_class:
                self._in_table = True
            return
        if not self._in_table:
            return
        if tag == "thead":
            self._in_head = True
        elif tag == "tbody":
            self._in_body = True
        elif tag == "tr":
            self._in_row = True
            self._current_row = []
        elif tag in {"th", "td"} and self._in_row:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if not self._in_table:
            return
        if tag in {"th", "td"} and self._in_cell:
            self._current_row.append("".join(self._current_cell).strip())
            self._current_cell = []
            self._in_cell = False
            return
        if tag == "tr" and self._in_row:
            if self._current_row:
                if self._in_head:
                    self._header_rows.append(list(self._current_row))
                elif self._in_body and len(self._body_rows) < 5:
                    self._body_rows.append(list(self._current_row))
            self._current_row = []
            self._in_row = False
            return
        if tag == "thead":
            self._in_head = False
        elif tag == "tbody":
            self._in_body = False
        elif tag == "table":
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._current_cell.append(data)

    def columns(self) -> list[str]:
        if not self._header_rows:
            return []
        header = [cell for cell in self._header_rows[-1] if cell]
        return header

    def rows(self) -> list[dict[str, JSONValue]]:
        columns = self.columns()
        if not columns:
            return []
        rows: list[dict[str, JSONValue]] = []
        for raw_row in self._body_rows[:3]:
            row = list(raw_row)
            if len(row) == len(columns) + 1:
                row = row[1:]
            if len(row) != len(columns):
                continue
            rows.append(
                {
                    column: _coerce_cell_value(value)
                    for column, value in zip(columns, row, strict=False)
                }
            )
        return rows


def _coerce_cell_value(value: str) -> JSONValue:
    lowered = value.strip().lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"none", "nan"}:
        return value.strip()
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value.strip()


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
