from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import cast

import pytest

from agentnb.errors import InvalidInputError
from agentnb.invocation import (
    ROOT_OPTION_SPECS,
    CommandIntent,
    ImplicitExecIntent,
    InvocationResolver,
)


class FakeStdin(StringIO):
    def __init__(self, value: str, *, is_tty: bool) -> None:
        super().__init__(value)
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


KNOWN_COMMANDS = ("start", "exec", "status", "wait", "history", "runs", "sessions")


@pytest.mark.parametrize("root_flag", [spec.flag for spec in ROOT_OPTION_SPECS])
def test_resolve_invocation_intent_moves_root_flags_ahead_of_command(root_flag: str) -> None:
    resolver = InvocationResolver()

    intent = resolver.resolve_invocation_intent(
        ["status", root_flag, "--project", "/tmp/project"],
        known_commands=KNOWN_COMMANDS,
        cwd=Path("/tmp/project"),
        stdin=FakeStdin("", is_tty=True),
    )

    assert intent.kind == "command"
    command_intent = cast(CommandIntent, intent)
    assert command_intent.command_name == "status"
    assert command_intent.root_flags == (root_flag,)
    assert command_intent.argv == (root_flag, "status", "--project", "/tmp/project")


def test_resolve_invocation_intent_keeps_double_dash_suffix_unchanged() -> None:
    resolver = InvocationResolver()

    intent = resolver.resolve_invocation_intent(
        ["exec", "--json", "--", "--agent"],
        known_commands=KNOWN_COMMANDS,
        cwd=Path("/tmp/project"),
        stdin=FakeStdin("", is_tty=True),
    )

    assert intent.kind == "command"
    command_intent = cast(CommandIntent, intent)
    assert command_intent.command_name == "exec"
    assert command_intent.root_flags == ("--json",)
    assert command_intent.argv == ("--json", "exec", "--", "--agent")


def test_resolve_invocation_intent_infers_argument_exec_from_unknown_positional() -> None:
    resolver = InvocationResolver()

    intent = resolver.resolve_invocation_intent(
        ["--project", "/tmp/project", "1 + 1", "--json"],
        known_commands=KNOWN_COMMANDS,
        cwd=Path("/tmp/project"),
        stdin=FakeStdin("", is_tty=True),
    )

    assert intent.kind == "implicit_exec"
    exec_intent = cast(ImplicitExecIntent, intent)
    assert exec_intent.source_kind == "argument"
    assert exec_intent.path is None
    assert exec_intent.root_flags == ("--json",)
    assert exec_intent.argv == (
        "--json",
        "exec",
        "--project",
        "/tmp/project",
        "1 + 1",
    )


def test_resolve_invocation_intent_infers_file_exec_from_existing_path(project_dir: Path) -> None:
    resolver = InvocationResolver()
    script = project_dir / "analysis.py"
    script.write_text("value = 1\nvalue + 1\n", encoding="utf-8")

    intent = resolver.resolve_invocation_intent(
        [str(script)],
        known_commands=KNOWN_COMMANDS,
        cwd=project_dir,
        stdin=FakeStdin("", is_tty=True),
    )

    assert intent.kind == "implicit_exec"
    exec_intent = cast(ImplicitExecIntent, intent)
    assert exec_intent.source_kind == "file"
    assert exec_intent.path == script
    assert exec_intent.argv == ("exec", "--file", str(script))


def test_resolve_invocation_intent_infers_stdin_exec_without_command() -> None:
    resolver = InvocationResolver()

    intent = resolver.resolve_invocation_intent(
        ["--project", "/tmp/project", "--json"],
        known_commands=KNOWN_COMMANDS,
        cwd=Path("/tmp/project"),
        stdin=FakeStdin("print('hello')\n", is_tty=False),
    )

    assert intent.kind == "implicit_exec"
    exec_intent = cast(ImplicitExecIntent, intent)
    assert exec_intent.source_kind == "stdin"
    assert exec_intent.argv == (
        "--json",
        "exec",
        "--project",
        "/tmp/project",
    )


def test_resolve_invocation_intent_preserves_explicit_no_startup_for_hot_path() -> None:
    resolver = InvocationResolver()

    intent = resolver.resolve_invocation_intent(
        ["--no-ensure-started", "1 + 1"],
        known_commands=KNOWN_COMMANDS,
        cwd=Path("/tmp/project"),
        stdin=FakeStdin("", is_tty=True),
    )

    assert intent.kind == "implicit_exec"
    exec_intent = cast(ImplicitExecIntent, intent)
    assert exec_intent.argv == ("exec", "--no-ensure-started", "1 + 1")


def test_resolve_invocation_intent_does_not_infer_stdin_exec_for_empty_non_tty() -> None:
    resolver = InvocationResolver()

    intent = resolver.resolve_invocation_intent(
        [],
        known_commands=KNOWN_COMMANDS,
        cwd=Path("/tmp/project"),
        stdin=FakeStdin("", is_tty=False),
    )

    assert intent.kind == "command"
    command_intent = cast(CommandIntent, intent)
    assert command_intent.command_name is None
    assert command_intent.argv == ()


def test_resolve_invocation_intent_help_without_command_stays_command_intent() -> None:
    resolver = InvocationResolver()

    intent = resolver.resolve_invocation_intent(
        ["--help"],
        known_commands=KNOWN_COMMANDS,
        cwd=Path("/tmp/project"),
        stdin=FakeStdin("print('hello')\n", is_tty=False),
    )

    assert intent.kind == "command"
    command_intent = cast(CommandIntent, intent)
    assert command_intent.command_name is None
    assert command_intent.root_flags == ()
    assert command_intent.argv == ("--help",)


def test_resolve_exec_source_prefers_argument() -> None:
    resolver = InvocationResolver()

    source = resolver.resolve_exec_source(
        code="1 + 1",
        filepath=None,
        stdin=FakeStdin("", is_tty=True),
    )

    assert source.source_kind == "argument"
    assert source.code == "1 + 1"
    assert source.path is None


def test_resolve_exec_source_reads_file(project_dir: Path) -> None:
    resolver = InvocationResolver()
    script = project_dir / "snippet.py"
    script.write_text("value = 1\nvalue + 1\n", encoding="utf-8")

    source = resolver.resolve_exec_source(
        code=None,
        filepath=script,
        stdin=FakeStdin("", is_tty=True),
    )

    assert source.source_kind == "file"
    assert source.path == script
    assert source.code == "value = 1\nvalue + 1\n"


def test_resolve_exec_source_reads_stdin_when_present() -> None:
    resolver = InvocationResolver()

    source = resolver.resolve_exec_source(
        code=None,
        filepath=None,
        stdin=FakeStdin("print('hello')\n", is_tty=False),
    )

    assert source.source_kind == "stdin"
    assert source.code == "print('hello')\n"


@pytest.mark.parametrize(
    ("filepath", "stdin", "message"),
    [
        (Path("/tmp/missing.py"), FakeStdin("", is_tty=True), "File not found: /tmp/missing.py"),
        (None, FakeStdin(" \n", is_tty=False), "No code provided through stdin"),
        (
            None,
            FakeStdin("", is_tty=True),
            "No code provided. Use code argument, --file, or pipe via stdin",
        ),
    ],
)
def test_resolve_exec_source_rejects_missing_inputs(
    filepath: Path | None,
    stdin: FakeStdin,
    message: str,
) -> None:
    resolver = InvocationResolver()

    with pytest.raises(InvalidInputError, match=message):
        resolver.resolve_exec_source(code=None, filepath=filepath, stdin=stdin)
