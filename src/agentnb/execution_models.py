from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

from .errors import AgentNBException
from .execution_output import (
    ExecutionOutput,
    OutputItem,
    compatibility_output,
    execution_output_from_events,
    execution_output_from_legacy_fields,
)
from .payloads import JSONValue

if TYPE_CHECKING:
    from .contracts import ExecutionEvent, ExecutionResult

FailureOrigin = Literal["kernel", "control"]

_CONTROL_ERROR_CODES = frozenset(
    {
        "SESSION_BUSY",
        "NO_KERNEL",
        "KERNEL_NOT_READY",
        "SESSION_NOT_FOUND",
        "AMBIGUOUS_SESSION",
    }
)


@dataclass(slots=True, frozen=True)
class ExecutionTranscript:
    output: ExecutionOutput

    @classmethod
    def from_output(cls, output: ExecutionOutput) -> ExecutionTranscript:
        return cls(
            output=ExecutionOutput(
                items=list(output.items),
                execution_count=output.execution_count,
            )
        )

    @classmethod
    def from_events(
        cls,
        events: list[ExecutionEvent],
        execution_count: int | None = None,
    ) -> ExecutionTranscript:
        return cls(output=execution_output_from_events(events, execution_count=execution_count))

    @classmethod
    def from_legacy_fields(
        cls,
        *,
        stdout: str = "",
        stderr: str = "",
        result: str | None = None,
        ename: str | None = None,
        evalue: str | None = None,
        traceback: list[str] | None = None,
        status: Literal["ok", "error"] = "ok",
        execution_count: int | None = None,
    ) -> ExecutionTranscript:
        return cls(
            output=execution_output_from_legacy_fields(
                stdout=stdout,
                stderr=stderr,
                result=result,
                ename=ename,
                evalue=evalue,
                traceback=traceback,
                status=status,
                execution_count=execution_count,
            )
        )

    @property
    def execution_count(self) -> int | None:
        return self.output.execution_count

    @property
    def outputs(self) -> list[OutputItem]:
        return list(self.output.items)

    @property
    def events(self) -> list[ExecutionEvent]:
        return self.output.to_events()

    @property
    def stdout(self) -> str:
        return compatibility_output(self.output).stdout

    @property
    def stderr(self) -> str:
        return compatibility_output(self.output).stderr

    @property
    def result(self) -> str | None:
        return compatibility_output(self.output).result

    @property
    def status(self) -> Literal["ok", "error"]:
        return compatibility_output(self.output).status

    @property
    def result_preview(self) -> object:
        return self.output.result_preview()

    @property
    def error_details(self) -> tuple[str | None, str | None, list[str] | None]:
        projected = compatibility_output(self.output)
        return projected.ename, projected.evalue, projected.traceback


@dataclass(slots=True, frozen=True)
class ExecutionOutcome:
    status: Literal["ok", "error"]
    duration_ms: int
    execution_count: int | None
    transcript: ExecutionTranscript
    ename: str | None
    evalue: str | None
    traceback: list[str] | None
    failure_origin: FailureOrigin | None = None
    error_data: dict[str, JSONValue] | None = None

    @classmethod
    def from_transcript(
        cls,
        *,
        transcript: ExecutionTranscript,
        status: Literal["ok", "error"],
        duration_ms: int,
        ename: str | None = None,
        evalue: str | None = None,
        traceback: list[str] | None = None,
        failure_origin: FailureOrigin | None = None,
        error_data: Mapping[str, JSONValue] | None = None,
        prefer_explicit_error: bool = False,
    ) -> ExecutionOutcome:
        projected_status = transcript.status
        projected_ename, projected_evalue, projected_traceback = transcript.error_details
        explicit_error = (
            status == "error" or ename is not None or evalue is not None or traceback is not None
        )
        if projected_status == "error":
            final_status: Literal["ok", "error"] = "error"
            if prefer_explicit_error and (
                ename is not None or evalue is not None or traceback is not None
            ):
                final_ename = ename
                final_evalue = evalue
                final_traceback = list(traceback) if traceback is not None else None
            else:
                final_ename = projected_ename
                final_evalue = projected_evalue
                final_traceback = projected_traceback
        elif explicit_error:
            final_status = "error"
            final_ename = ename
            final_evalue = evalue
            final_traceback = list(traceback) if traceback is not None else None
        else:
            final_status = projected_status
            final_ename = projected_ename
            final_evalue = projected_evalue
            final_traceback = projected_traceback
        return cls(
            status=final_status,
            duration_ms=duration_ms,
            execution_count=transcript.execution_count,
            transcript=transcript,
            ename=final_ename,
            evalue=final_evalue,
            traceback=final_traceback,
            failure_origin=failure_origin,
            error_data=dict(error_data) if error_data is not None else None,
        )

    @classmethod
    def from_runtime_fields(
        cls,
        *,
        status: Literal["ok", "error"],
        duration_ms: int,
        stdout: str = "",
        stderr: str = "",
        result: str | None = None,
        ename: str | None = None,
        evalue: str | None = None,
        traceback: list[str] | None = None,
        execution_count: int | None = None,
        failure_origin: FailureOrigin | None = None,
        error_data: Mapping[str, JSONValue] | None = None,
    ) -> ExecutionOutcome:
        transcript = ExecutionTranscript.from_legacy_fields(
            stdout=stdout,
            stderr=stderr,
            result=result,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
            status=status,
            execution_count=execution_count,
        )
        return cls.from_transcript(
            transcript=transcript,
            status=status,
            duration_ms=duration_ms,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
            failure_origin=failure_origin,
            error_data=error_data,
        )

    @classmethod
    def from_execution_result(cls, result: ExecutionResult) -> ExecutionOutcome:
        if result.outputs:
            transcript = ExecutionTranscript.from_output(
                ExecutionOutput(
                    items=list(result.outputs),
                    execution_count=result.execution_count,
                )
            )
        elif result.events:
            transcript = ExecutionTranscript.from_events(
                result.events,
                execution_count=result.execution_count,
            )
        else:
            transcript = ExecutionTranscript.from_legacy_fields(
                stdout=result.stdout,
                stderr=result.stderr,
                result=result.result,
                ename=result.ename,
                evalue=result.evalue,
                traceback=result.traceback,
                status=result.status,
                execution_count=result.execution_count,
            )
        return cls.from_transcript(
            transcript=transcript,
            status=result.status,
            duration_ms=result.duration_ms,
            ename=result.ename,
            evalue=result.evalue,
            traceback=result.traceback,
        )

    @classmethod
    def from_exception(
        cls,
        error: Exception,
        *,
        duration_ms: int,
        failure_origin: FailureOrigin | None = None,
    ) -> ExecutionOutcome:
        ename = type(error).__name__
        evalue = str(error)
        traceback = None
        error_data: dict[str, JSONValue] | None = None
        if isinstance(error, AgentNBException):
            ename = error.ename or ename
            evalue = error.evalue or error.message
            traceback = error.traceback
            error_data = _json_object(error.error_context.to_data())
            if not error_data:
                error_data = None
            if failure_origin is None:
                failure_origin = "control" if error.code in _CONTROL_ERROR_CODES else "kernel"
        elif failure_origin is None:
            failure_origin = "kernel"
        transcript = ExecutionTranscript.from_legacy_fields(
            status="error",
            execution_count=None,
        )
        return cls.from_transcript(
            transcript=transcript,
            status="error",
            duration_ms=duration_ms,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
            failure_origin=failure_origin,
            error_data=error_data,
        )

    @property
    def stdout(self) -> str:
        return self.transcript.stdout

    @property
    def stderr(self) -> str:
        return self.transcript.stderr

    @property
    def result(self) -> str | None:
        return self.transcript.result

    @property
    def outputs(self) -> list[OutputItem]:
        return self.transcript.outputs

    @property
    def events(self) -> list[ExecutionEvent]:
        return self.transcript.events

    @property
    def result_preview(self) -> object:
        return self.transcript.result_preview

    def result_preview_text(self, limit: int = 160) -> str | None:
        return _summarize_text(self.result, limit=limit)

    def stdout_preview_text(self, limit: int = 160) -> str | None:
        return _summarize_text(self.stdout, limit=limit)


def _summarize_text(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    compact = " ".join(value.strip().split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _json_object(payload: Mapping[str, object]) -> dict[str, JSONValue]:
    normalized: dict[str, JSONValue] = {}
    for key, value in payload.items():
        normalized_value = _json_value(value)
        if normalized_value is None and value is not None:
            continue
        normalized[str(key)] = normalized_value
    return normalized


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (bool, int, float, str)):
        return cast(JSONValue, value)
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, JSONValue] = {}
        for key, item in value.items():
            normalized[str(key)] = _json_value(item)
        return normalized
    return str(value)
