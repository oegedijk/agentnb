from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from .payloads import JSONValue


@dataclass(slots=True, frozen=True)
class VariableEntry:
    name: str
    repr_text: str
    type_name: str | None = None


@dataclass(slots=True, frozen=True)
class NamespaceDeltaEntry:
    name: str
    repr_text: str
    change: Literal["new", "updated"]
    type_name: str | None = None


@dataclass(slots=True, frozen=True)
class NamespaceDelta:
    entries: list[NamespaceDeltaEntry] = field(default_factory=list)
    new_count: int = 0
    updated_count: int = 0
    truncated: bool = False


@dataclass(slots=True, frozen=True)
class DataframePreviewData:
    shape: list[int] | None = None
    columns: list[str] = field(default_factory=list)
    column_count: int | None = None
    columns_shown: int | None = None
    dtypes: dict[str, str] | None = None
    dtypes_shown: int | None = None
    head: list[dict[str, JSONValue]] | None = None
    head_rows_shown: int | None = None
    null_counts: dict[str, int] | None = None
    null_count_fields_shown: int | None = None


@dataclass(slots=True, frozen=True)
class MappingPreviewData:
    length: int
    keys: list[str]
    sample: dict[str, JSONValue]
    keys_shown: int | None = None
    sample_items_shown: int | None = None
    sample_truncated: bool | None = None


@dataclass(slots=True, frozen=True)
class SequencePreviewData:
    length: int
    sample: list[JSONValue]
    item_type: str | None = None
    sample_keys: list[str] = field(default_factory=list)
    sample_items_shown: int | None = None
    sample_keys_shown: int | None = None
    sample_truncated: bool | None = None


InspectPreviewData: TypeAlias = DataframePreviewData | MappingPreviewData | SequencePreviewData


@dataclass(slots=True, frozen=True)
class InspectValue:
    name: str
    type_name: str
    repr_text: str | None = None
    members: list[str] = field(default_factory=list)
    doc: str | None = None
    preview: InspectPreviewData | None = None


@dataclass(slots=True, frozen=True)
class FailedModule:
    module: str
    error_type: str
    message: str


@dataclass(slots=True, frozen=True)
class ReloadResult:
    mode: Literal["module", "project"] | None = None
    requested_module: str | None = None
    reloaded_modules: list[str] = field(default_factory=list)
    failed_modules: list[FailedModule] = field(default_factory=list)
    skipped_modules: list[str] = field(default_factory=list)
    rebound_names: list[str] = field(default_factory=list)
    stale_names: list[str] = field(default_factory=list)
    excluded_module_count: int | None = None
    notes: list[str] = field(default_factory=list)
