from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from io import UnsupportedOperation
from pathlib import Path
from typing import Literal, Protocol, TypeAlias, cast

from .errors import InvalidInputError


@dataclass(slots=True, frozen=True)
class RootOptionSpec:
    flag: str
    param_decls: tuple[str, ...]
    help_text: str


ROOT_OPTION_SPECS = (
    RootOptionSpec(
        flag="--json",
        param_decls=("--json", "root_as_json"),
        help_text="Output all commands as JSON",
    ),
    RootOptionSpec(
        flag="--agent",
        param_decls=("--agent",),
        help_text="Agent preset: JSON output with deterministic, low-noise defaults.",
    ),
    RootOptionSpec(
        flag="--quiet",
        param_decls=("--quiet",),
        help_text="Reduce non-essential human output",
    ),
    RootOptionSpec(
        flag="--no-suggestions",
        param_decls=("--no-suggestions",),
        help_text="Suppress next-step suggestions",
    ),
)


@dataclass(slots=True, frozen=True)
class InvocationOptionSpec:
    names: tuple[str, ...]
    kind: Literal["root", "exec", "help"]
    takes_value: bool = False


@dataclass(slots=True, frozen=True)
class CommandShape:
    name: str
    kind: Literal["leaf", "group"]
    prefix_exec_position: Literal["after_command", "after_subcommand"] = "after_command"
    subcommands: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class UnknownCommandAdvice:
    suggestions: tuple[str, ...]

    def message(self, command_name: str) -> str:
        suggestion_text = _join_suggestions(self.suggestions)
        return f"Unknown command '{command_name}'. Did you mean {suggestion_text}?"


INVOCATION_OPTION_SPECS = (
    *(InvocationOptionSpec(names=(spec.flag,), kind="root") for spec in ROOT_OPTION_SPECS),
    InvocationOptionSpec(names=("--help", "-h"), kind="help"),
    InvocationOptionSpec(names=("--project",), kind="exec", takes_value=True),
    InvocationOptionSpec(names=("--session",), kind="exec", takes_value=True),
    InvocationOptionSpec(names=("--timeout",), kind="exec", takes_value=True),
    InvocationOptionSpec(names=("--file", "-f"), kind="exec", takes_value=True),
    InvocationOptionSpec(names=("--ensure-started",), kind="exec"),
    InvocationOptionSpec(names=("--no-ensure-started",), kind="exec"),
    InvocationOptionSpec(names=("--background",), kind="exec"),
    InvocationOptionSpec(names=("--stream",), kind="exec"),
    InvocationOptionSpec(names=("--stdout-only",), kind="exec"),
    InvocationOptionSpec(names=("--stderr-only",), kind="exec"),
    InvocationOptionSpec(names=("--result-only",), kind="exec"),
    InvocationOptionSpec(names=("--no-truncate",), kind="exec"),
    InvocationOptionSpec(names=("--fresh",), kind="exec"),
)


COMMAND_SHAPES = (
    CommandShape(name="start", kind="leaf"),
    CommandShape(name="exec", kind="leaf"),
    CommandShape(name="vars", kind="leaf"),
    CommandShape(name="inspect", kind="leaf"),
    CommandShape(name="reload", kind="leaf"),
    CommandShape(name="status", kind="leaf"),
    CommandShape(name="wait", kind="leaf"),
    CommandShape(name="history", kind="leaf"),
    CommandShape(name="interrupt", kind="leaf"),
    CommandShape(name="reset", kind="leaf"),
    CommandShape(name="stop", kind="leaf"),
    CommandShape(name="doctor", kind="leaf"),
    CommandShape(
        name="runs",
        kind="group",
        prefix_exec_position="after_subcommand",
        subcommands=("list", "show", "wait", "follow", "cancel", "help"),
    ),
    CommandShape(
        name="sessions",
        kind="group",
        prefix_exec_position="after_subcommand",
        subcommands=("list", "delete", "help"),
    ),
)

_UNKNOWN_COMMAND_ADVICE = {
    "list": UnknownCommandAdvice(suggestions=("agentnb sessions list", "agentnb runs list")),
    "log": UnknownCommandAdvice(suggestions=("agentnb history",)),
    "logs": UnknownCommandAdvice(suggestions=("agentnb history",)),
}


class StdinReader(Protocol):
    def isatty(self) -> bool: ...

    def read(self) -> str: ...


class SeekableStdin(StdinReader, Protocol):
    def seekable(self) -> bool: ...

    def tell(self) -> int: ...

    def seek(self, position: int) -> int: ...


class BufferedStdin(StdinReader, Protocol):
    @property
    def buffer(self) -> object: ...


@dataclass(slots=True, frozen=True)
class CommandIntent:
    kind: Literal["command"] = "command"
    argv: tuple[str, ...] = ()
    command_name: str | None = None
    root_flags: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class ImplicitExecIntent:
    kind: Literal["implicit_exec"] = "implicit_exec"
    argv: tuple[str, ...] = ()
    root_flags: tuple[str, ...] = ()
    source_kind: Literal["argument", "file", "stdin"] = "argument"
    path: Path | None = None


InvocationIntent: TypeAlias = CommandIntent | ImplicitExecIntent


@dataclass(slots=True, frozen=True)
class ExecSourceIntent:
    code: str
    source_kind: str
    path: Path | None = None


@dataclass(slots=True, frozen=True)
class _ScannedArgs:
    prefix_root_flags: tuple[str, ...]
    prefix_exec_tokens: tuple[str, ...]
    command_candidate: str | None
    tail_root_flags: tuple[str, ...]
    tail_tokens_without_root: tuple[str, ...]
    tail_positionals: tuple[str, ...]
    tail_positional_indexes: tuple[int, ...]
    suffix: tuple[str, ...]
    saw_help: bool
    saw_unknown_option: bool
    file_option_path: Path | None


class InvocationResolver:
    def __init__(
        self,
        *,
        root_options: Sequence[RootOptionSpec] = ROOT_OPTION_SPECS,
        option_specs: Sequence[InvocationOptionSpec] = INVOCATION_OPTION_SPECS,
        command_shapes: Sequence[CommandShape] = COMMAND_SHAPES,
    ) -> None:
        self._root_flag_names = frozenset(spec.flag for spec in root_options)
        option_map: dict[str, InvocationOptionSpec] = {}
        for spec in option_specs:
            for name in spec.names:
                option_map[name] = spec
        self._option_specs = option_map
        self._command_shapes = {shape.name: shape for shape in command_shapes}
        self._subcommand_words = frozenset(
            subcommand for shape in command_shapes for subcommand in shape.subcommands
        )

    def resolve_invocation_intent(
        self,
        args: Sequence[str],
        *,
        known_commands: Sequence[str],
        cwd: Path,
        stdin: StdinReader,
    ) -> InvocationIntent:
        raw_args = list(args)
        scanned = self._scan_args(raw_args)
        known_command_names = frozenset(known_commands)
        root_flags = (*scanned.prefix_root_flags, *scanned.tail_root_flags)
        command_name = scanned.command_candidate
        typo_advice = self.unknown_command_advice(command_name)

        if command_name in known_command_names:
            if scanned.saw_unknown_option:
                return CommandIntent(
                    argv=tuple(raw_args),
                    command_name=command_name,
                )
            return CommandIntent(
                argv=self._canonical_command_argv(command_name=command_name, scanned=scanned),
                command_name=command_name,
                root_flags=tuple(root_flags),
            )

        if typo_advice is not None:
            return CommandIntent(argv=tuple(raw_args), command_name=None)

        if self._should_infer_implicit_exec(scanned, stdin=stdin):
            return self._implicit_exec_intent(
                scanned=scanned,
                cwd=cwd,
                root_flags=tuple(root_flags),
            )

        return CommandIntent(
            argv=tuple(raw_args),
            command_name=command_name if command_name in known_command_names else None,
        )

    def unknown_command_advice(self, command_name: str | None) -> UnknownCommandAdvice | None:
        if not isinstance(command_name, str):
            return None
        normalized = command_name.strip().lower()
        if not normalized:
            return None
        return _UNKNOWN_COMMAND_ADVICE.get(normalized)

    def resolve_exec_source(
        self,
        *,
        code: str | None,
        filepath: Path | None,
        stdin: StdinReader,
    ) -> ExecSourceIntent:
        if filepath is not None:
            if not filepath.exists():
                raise InvalidInputError(f"File not found: {filepath}")
            return ExecSourceIntent(
                code=filepath.read_text(encoding="utf-8"),
                source_kind="file",
                path=filepath,
            )

        if code is not None:
            return ExecSourceIntent(code=code, source_kind="argument")

        if not stdin.isatty():
            stdin_data = stdin.read()
            if stdin_data.strip():
                return ExecSourceIntent(code=stdin_data, source_kind="stdin")
            raise InvalidInputError("No code provided through stdin")

        raise InvalidInputError("No code provided. Use code argument, --file, or pipe via stdin")

    def _scan_args(self, args: Sequence[str]) -> _ScannedArgs:
        boundary = args.index("--") if "--" in args else len(args)
        prefix_root_flags: list[str] = []
        prefix_exec_tokens: list[str] = []
        tail_root_flags: list[str] = []
        tail_tokens_without_root: list[str] = []
        tail_positionals: list[str] = []
        tail_positional_indexes: list[int] = []
        saw_help = False
        saw_unknown_option = False
        file_option_path: Path | None = None
        command_candidate: str | None = None

        index = 0
        while index < boundary:
            token = args[index]
            if not token.startswith("-"):
                command_candidate = token
                index += 1
                break
            consumed, spec, option_path = self._consume_option(args, index=index, boundary=boundary)
            if spec is None:
                saw_unknown_option = True
                break
            if spec.kind == "help":
                saw_help = True
            elif spec.kind == "root":
                prefix_root_flags.extend(consumed)
            else:
                prefix_exec_tokens.extend(consumed)
                if option_path is not None:
                    file_option_path = option_path
            index += len(consumed)

        while index < boundary:
            token = args[index]
            if token.startswith("-"):
                consumed, spec, option_path = self._consume_option(
                    args, index=index, boundary=boundary
                )
                if spec is None:
                    saw_unknown_option = True
                    tail_tokens_without_root.append(token)
                    index += 1
                    continue
                if spec.kind == "help":
                    saw_help = True
                    tail_tokens_without_root.extend(consumed)
                elif spec.kind == "root":
                    tail_root_flags.extend(consumed)
                else:
                    tail_tokens_without_root.extend(consumed)
                    if option_path is not None:
                        file_option_path = option_path
                index += len(consumed)
                continue
            tail_tokens_without_root.append(token)
            tail_positionals.append(token)
            tail_positional_indexes.append(len(tail_tokens_without_root) - 1)
            index += 1

        return _ScannedArgs(
            prefix_root_flags=tuple(prefix_root_flags),
            prefix_exec_tokens=tuple(prefix_exec_tokens),
            command_candidate=command_candidate,
            tail_root_flags=tuple(tail_root_flags),
            tail_tokens_without_root=tuple(tail_tokens_without_root),
            tail_positionals=tuple(tail_positionals),
            tail_positional_indexes=tuple(tail_positional_indexes),
            suffix=tuple(args[boundary:]),
            saw_help=saw_help,
            saw_unknown_option=saw_unknown_option,
            file_option_path=file_option_path,
        )

    def _consume_option(
        self,
        args: Sequence[str],
        *,
        index: int,
        boundary: int,
    ) -> tuple[tuple[str, ...], InvocationOptionSpec | None, Path | None]:
        token = args[index]
        option_name = token
        inline_value: str | None = None

        if token.startswith("--") and "=" in token:
            option_name, inline_value = token.split("=", 1)
        elif token.startswith("-") and not token.startswith("--") and len(token) > 2:
            short_name = token[:2]
            spec = self._option_specs.get(short_name)
            if spec is not None and spec.takes_value:
                option_name = short_name
                inline_value = token[2:]

        spec = self._option_specs.get(option_name)
        if spec is None:
            return ((token,), None, None)

        if spec.takes_value:
            if inline_value is not None:
                value = inline_value
                consumed = (token,)
            elif index + 1 < boundary:
                value = args[index + 1]
                consumed = (token, value)
            else:
                value = ""
                consumed = (token,)
            option_path = Path(value) if option_name in {"--file", "-f"} and value else None
            return (consumed, spec, option_path)

        return ((token,), spec, None)

    def _should_infer_implicit_exec(self, scanned: _ScannedArgs, *, stdin: StdinReader) -> bool:
        if (
            scanned.saw_help
            or scanned.saw_unknown_option
            or scanned.suffix
            or scanned.tail_positionals
        ):
            return False
        if scanned.command_candidate is not None:
            return scanned.command_candidate not in self._subcommand_words
        if scanned.file_option_path is not None:
            return True
        return self._stdin_has_data(stdin)

    def _canonical_command_argv(
        self,
        *,
        command_name: str,
        scanned: _ScannedArgs,
    ) -> tuple[str, ...]:
        shape = self._command_shapes.get(command_name)
        insertion_index = self._prefix_exec_insertion_index(shape=shape, scanned=scanned)
        tail_tokens = list(scanned.tail_tokens_without_root)
        if scanned.prefix_exec_tokens:
            tail_tokens = [
                *tail_tokens[:insertion_index],
                *scanned.prefix_exec_tokens,
                *tail_tokens[insertion_index:],
            ]
        return (
            *scanned.prefix_root_flags,
            *scanned.tail_root_flags,
            command_name,
            *tail_tokens,
            *scanned.suffix,
        )

    def _prefix_exec_insertion_index(
        self,
        *,
        shape: CommandShape | None,
        scanned: _ScannedArgs,
    ) -> int:
        if not scanned.prefix_exec_tokens:
            return 0
        if shape is None or shape.prefix_exec_position != "after_subcommand":
            return 0
        if not scanned.tail_positional_indexes:
            return 0
        return scanned.tail_positional_indexes[0] + 1

    def _implicit_exec_intent(
        self,
        *,
        scanned: _ScannedArgs,
        cwd: Path,
        root_flags: tuple[str, ...],
    ) -> ImplicitExecIntent:
        exec_tokens = [*scanned.prefix_exec_tokens, *scanned.tail_tokens_without_root]

        source_kind: Literal["argument", "file", "stdin"] = "stdin"
        path: Path | None = None

        if scanned.command_candidate is not None:
            path = self._existing_file_path(scanned.command_candidate, cwd=cwd)
            if path is None:
                source_kind = "argument"
                exec_tokens.append(scanned.command_candidate)
            else:
                source_kind = "file"
                exec_tokens.extend(("--file", scanned.command_candidate))
        elif scanned.file_option_path is not None:
            source_kind = "file"
            path = scanned.file_option_path

        return ImplicitExecIntent(
            argv=(*root_flags, "exec", *exec_tokens),
            root_flags=root_flags,
            source_kind=source_kind,
            path=path,
        )

    @staticmethod
    def _existing_file_path(token: str, *, cwd: Path) -> Path | None:
        if len(token) > 255 or "\n" in token:
            return None
        candidate = Path(token)
        resolved = candidate if candidate.is_absolute() else cwd / candidate
        try:
            if resolved.exists() and resolved.is_file():
                return candidate
        except OSError:
            return None
        return None

    @staticmethod
    def _stdin_has_data(stdin: StdinReader) -> bool:
        if stdin.isatty():
            return False

        seekable = getattr(stdin, "seekable", None)
        if callable(seekable):
            try:
                if seekable():
                    seekable_stdin = cast(SeekableStdin, stdin)
                    position = seekable_stdin.tell()
                    data = stdin.read()
                    seekable_stdin.seek(position)
                    return bool(data.strip())
            except (OSError, UnsupportedOperation):
                pass

        buffer = getattr(cast(BufferedStdin, stdin), "buffer", None)
        peek = getattr(buffer, "peek", None)
        if callable(peek):
            try:
                data = peek(1)
            except OSError:
                return False
            if isinstance(data, bytes):
                return bool(data.strip())
            return bool(str(data).strip())

        return False


def _join_suggestions(suggestions: Sequence[str]) -> str:
    rendered = tuple(f"`{suggestion}`" for suggestion in suggestions if suggestion)
    if len(rendered) == 1:
        return rendered[0]
    if not rendered:
        return "`agentnb --help`"
    head = ", ".join(rendered[:-1])
    return f"{head} or {rendered[-1]}"
