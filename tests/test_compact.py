from __future__ import annotations

from agentnb.compact import (
    compact_collection_preview,
    compact_traceback,
)
from agentnb.payloads import SequencePreview


def test_compact_traceback_strips_ansi_and_middle_lines() -> None:
    traceback = [
        "\x1b[31mTraceback (most recent call last):\x1b[0m",
        '  File "main.py", line 1, in <module>',
        "line 3",
        "line 4",
        "line 5",
        "ValueError: bad value",
    ]

    compacted = compact_traceback(traceback)

    assert compacted == [
        "Traceback (most recent call last):",
        '  File "main.py", line 1, in <module>',
        "...",
        "line 4",
        "line 5",
        "ValueError: bad value",
    ]


def test_compact_collection_preview_limits_nested_values() -> None:
    preview: SequencePreview = {
        "kind": "sequence-like",
        "length": 5,
        "item_type": "dict",
        "sample_keys": ["id", "title", "body", "author", "meta", "ignored"],
        "sample": [
            {
                "id": 1,
                "title": "a" * 100,
                "body": "b" * 100,
                "author": "c" * 100,
                "meta": "d" * 100,
                "ignored": "e" * 100,
            }
            for _ in range(5)
        ],
    }

    compacted = compact_collection_preview(preview)

    assert compacted["kind"] == "sequence-like"
    assert compacted["length"] == 5
    assert compacted["item_type"] == "dict"
    assert compacted["sample_keys"] == ["id", "title", "body", "author", "meta"]
    assert len(compacted["sample"]) == 3
    first = compacted["sample"][0]
    assert isinstance(first, dict)
    assert set(first) == {"id", "title", "body", "author", "meta"}
