from __future__ import annotations

from dataclasses import dataclass

from .contracts import ExecutionResult
from .execution_models import ExecutionOutcome
from .history import (
    FailureOrigin,
    HistoryClassification,
    HistoryProvenanceDetail,
    HistoryRecord,
    HistoryStore,
    kernel_execution_record,
    user_command_record,
)


@dataclass(slots=True, frozen=True)
class CommandProvenance:
    classification: HistoryClassification
    detail: HistoryProvenanceDetail


@dataclass(slots=True, frozen=True)
class CommandRecordSpec:
    provenance: CommandProvenance
    label: str
    origin: str | None = None
    input_text: str | None = None
    code: str | None = None


@dataclass(slots=True, frozen=True)
class CommandRecording:
    command_type: str
    user: CommandRecordSpec
    internal: CommandRecordSpec | None = None

    def build_records(
        self,
        *,
        ts: str | None = None,
        session_id: str,
        execution_id: str | None = None,
        execution: ExecutionResult | None = None,
        outcome: ExecutionOutcome | None = None,
        error: Exception | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        failure_origin: FailureOrigin | None = None,
        stdout: str | None = None,
        result: str | None = None,
    ) -> list[HistoryRecord]:
        records: list[HistoryRecord] = []
        if self.internal is not None:
            records.append(
                self._build(
                    self.internal,
                    ts=ts,
                    session_id=session_id,
                    execution_id=execution_id,
                    execution=execution,
                    outcome=outcome,
                    error=error,
                    status=status,
                    duration_ms=duration_ms,
                    error_type=error_type,
                    failure_origin=failure_origin,
                    stdout=stdout,
                    result=result,
                )
            )
        records.append(
            self._build(
                self.user,
                ts=ts,
                session_id=session_id,
                execution_id=execution_id,
                execution=execution,
                outcome=outcome,
                error=error,
                status=status,
                duration_ms=duration_ms,
                error_type=error_type,
                failure_origin=failure_origin,
                stdout=stdout,
                result=result,
            )
        )
        return records

    def build_user_record(
        self,
        *,
        ts: str | None = None,
        session_id: str,
        execution_id: str | None = None,
        execution: ExecutionResult | None = None,
        outcome: ExecutionOutcome | None = None,
        error: Exception | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        failure_origin: FailureOrigin | None = None,
        stdout: str | None = None,
        result: str | None = None,
    ) -> HistoryRecord:
        return self._build(
            self.user,
            ts=ts,
            session_id=session_id,
            execution_id=execution_id,
            execution=execution,
            outcome=outcome,
            error=error,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            failure_origin=failure_origin,
            stdout=stdout,
            result=result,
        )

    def build_internal_record(
        self,
        *,
        ts: str | None = None,
        session_id: str,
        execution_id: str | None = None,
        execution: ExecutionResult | None = None,
        outcome: ExecutionOutcome | None = None,
        error: Exception | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        failure_origin: FailureOrigin | None = None,
        stdout: str | None = None,
        result: str | None = None,
    ) -> HistoryRecord | None:
        if self.internal is None:
            return None
        return self._build(
            self.internal,
            ts=ts,
            session_id=session_id,
            execution_id=execution_id,
            execution=execution,
            outcome=outcome,
            error=error,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            failure_origin=failure_origin,
            stdout=stdout,
            result=result,
        )

    def append_to(
        self,
        history: HistoryStore,
        *,
        ts: str | None = None,
        session_id: str,
        execution_id: str | None = None,
        execution: ExecutionResult | None = None,
        outcome: ExecutionOutcome | None = None,
        error: Exception | None = None,
        status: str | None = None,
        duration_ms: int | None = None,
        error_type: str | None = None,
        failure_origin: FailureOrigin | None = None,
        stdout: str | None = None,
        result: str | None = None,
    ) -> None:
        for record in self.build_records(
            ts=ts,
            session_id=session_id,
            execution_id=execution_id,
            execution=execution,
            outcome=outcome,
            error=error,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            failure_origin=failure_origin,
            stdout=stdout,
            result=result,
        ):
            history.append(record)

    def _build(
        self,
        spec: CommandRecordSpec,
        *,
        ts: str | None,
        session_id: str,
        execution_id: str | None,
        execution: ExecutionResult | None,
        outcome: ExecutionOutcome | None,
        error: Exception | None,
        status: str | None,
        duration_ms: int | None,
        error_type: str | None,
        failure_origin: FailureOrigin | None,
        stdout: str | None,
        result: str | None,
    ) -> HistoryRecord:
        if spec.provenance.detail == "user_command":
            return user_command_record(
                ts=ts,
                session_id=session_id,
                execution_id=execution_id,
                classification=spec.provenance.classification,
                command_type=self.command_type,
                label=spec.label,
                input_text=spec.input_text,
                code=spec.code,
                origin=spec.origin,
                execution=execution,
                outcome=outcome,
                error=error,
                status=status,
                duration_ms=duration_ms,
                error_type=error_type,
                failure_origin=failure_origin,
                stdout=stdout,
                result=result,
            )
        return kernel_execution_record(
            ts=ts,
            session_id=session_id,
            execution_id=execution_id,
            classification=spec.provenance.classification,
            command_type=self.command_type,
            label=spec.label,
            code=spec.code,
            origin=spec.origin or "system",
            execution=execution,
            outcome=outcome,
            error=error,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            failure_origin=failure_origin,
            stdout=stdout,
            result=result,
        )


class CommandRecorder:
    def exec(self, *, code: str) -> CommandRecording:
        return CommandRecording(
            command_type="exec",
            user=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="replayable",
                    detail="user_command",
                ),
                label="exec",
                input_text=code,
                code=code,
                origin="execution_service",
            ),
            internal=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="internal",
                    detail="kernel_execution",
                ),
                label="exec kernel execution",
                code=code,
                origin="execution_service",
            ),
        )

    def reset(self) -> CommandRecording:
        return CommandRecording(
            command_type="reset",
            user=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="replayable",
                    detail="user_command",
                ),
                label="reset",
                origin="execution_service",
            ),
            internal=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="internal",
                    detail="kernel_execution",
                ),
                label="reset kernel state",
                origin="execution_service",
            ),
        )

    def vars(self, *, code: str) -> CommandRecording:
        return CommandRecording(
            command_type="vars",
            user=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="inspection",
                    detail="user_command",
                ),
                label="vars",
                origin="ops",
            ),
            internal=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="internal",
                    detail="kernel_execution",
                ),
                label="vars helper",
                code=code,
                origin="ops_helper",
            ),
        )

    def inspect(self, *, name: str, code: str) -> CommandRecording:
        return CommandRecording(
            command_type="inspect",
            user=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="inspection",
                    detail="user_command",
                ),
                label=f"inspect {name}",
                input_text=name,
                origin="ops",
            ),
            internal=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="internal",
                    detail="kernel_execution",
                ),
                label=f"inspect {name} helper",
                code=code,
                origin="ops_helper",
            ),
        )

    def reload(self, *, module_name: str | None, code: str) -> CommandRecording:
        label = "reload" if module_name is None else f"reload {module_name}"
        return CommandRecording(
            command_type="reload",
            user=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="control",
                    detail="user_command",
                ),
                label=label,
                input_text=module_name,
                origin="ops",
            ),
            internal=CommandRecordSpec(
                provenance=CommandProvenance(
                    classification="internal",
                    detail="kernel_execution",
                ),
                label=f"{label} helper",
                code=code,
                origin="ops_helper",
            ),
        )

    def for_execution(self, *, command_type: str, code: str | None) -> CommandRecording:
        if command_type == "exec":
            resolved_code = "" if code is None else code
            return self.exec(code=resolved_code)
        if command_type == "reset":
            return self.reset()
        raise ValueError(f"Unsupported execution command type: {command_type}")
