from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from agentnb.cli import main

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


def _payload(output: str) -> dict[str, object]:
    return json.loads(output)


def _write_module(project_dir: Path, name: str, body: str) -> None:
    (project_dir / f"{name}.py").write_text(body, encoding="utf-8")


def test_cli_json_envelope_for_exec_roundtrip(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 + 1"])
    assert exec_res.exit_code == 0

    payload = _payload(exec_res.output)
    assert payload["schema_version"] == "1.0"
    assert payload["status"] == "ok"
    assert payload["command"] == "exec"
    assert payload["session_id"] == "default"
    assert payload["data"]["result"] == "2"
    assert "events" not in payload["data"]

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


@pytest.mark.parametrize(
    ("args", "stdin", "expected_result", "expected_stdout"),
    [
        (["exec", "--json", "1 + 1"], None, "2", None),
        (["exec", "--json"], "print('hello from stdin')", None, "hello from stdin"),
    ],
)
def test_cli_exec_input_modes(
    cli_runner: CliRunner,
    project_dir: Path,
    args: list[str],
    stdin: str | None,
    expected_result: str | None,
    expected_stdout: str | None,
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    full_args = [*args[:1], "--project", str(project_dir), *args[1:]]
    exec_res = cli_runner.invoke(main, full_args, input=stdin)
    assert exec_res.exit_code == 0

    payload = _payload(exec_res.output)
    if expected_result is not None:
        assert payload["data"]["result"] == expected_result
    if expected_stdout is not None:
        assert payload["data"]["stdout"].strip() == expected_stdout

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_exec_returns_top_level_error_when_execution_fails(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 / 0"])
    assert exec_res.exit_code == 1

    payload = _payload(exec_res.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "EXECUTION_ERROR"
    assert payload["data"]["status"] == "error"
    assert payload["data"]["ename"] == "ZeroDivisionError"
    assert "traceback" not in payload["data"]
    assert "events" not in payload["data"]
    assert len(payload["error"]["traceback"]) <= 6

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_returns_no_kernel_error(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1+1"])
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "NO_KERNEL"


def test_cli_returns_kernel_not_ready_error_when_connection_exists_without_session(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    state_dir = project_dir / ".agentnb"
    state_dir.mkdir()
    (state_dir / "kernel-default.json").write_text("{}", encoding="utf-8")

    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1+1"])
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "KERNEL_NOT_READY"


def test_cli_doctor_returns_diagnostics(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["doctor", "--project", str(project_dir), "--json"])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["status"] == "ok"
    assert payload["command"] == "doctor"
    assert "checks" in payload["data"]


def test_cli_start_auto_install_is_opt_in(
    cli_runner: CliRunner,
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    start_mock = mocker.patch("agentnb.cli.runtime.start")
    start_mock.return_value = (
        mocker.Mock(
            to_dict=lambda: {
                "alive": True,
                "pid": 1234,
                "connection_file": str(project_dir / ".agentnb" / "kernel-default.json"),
                "started_at": "2026-03-09T00:00:00+00:00",
                "uptime_s": 0.0,
                "python": "python",
            }
        ),
        True,
    )

    result = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    assert start_mock.call_args.kwargs["auto_install"] is False


def test_cli_start_auto_install_flag_enables_installs(
    cli_runner: CliRunner,
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    start_mock = mocker.patch("agentnb.cli.runtime.start")
    start_mock.return_value = (
        mocker.Mock(
            to_dict=lambda: {
                "alive": True,
                "pid": 1234,
                "connection_file": str(project_dir / ".agentnb" / "kernel-default.json"),
                "started_at": "2026-03-09T00:00:00+00:00",
                "uptime_s": 0.0,
                "python": "python",
            }
        ),
        True,
    )

    result = cli_runner.invoke(
        main, ["start", "--project", str(project_dir), "--auto-install", "--json"]
    )

    assert result.exit_code == 0
    assert start_mock.call_args.kwargs["auto_install"] is True


def test_cli_root_help_is_shown_without_arguments(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(main, [])
    assert result.exit_code == 0
    assert "Run `agentnb --help`" in result.output
    assert "Recommended loop:" in result.output
    assert "Prefer --json for agent integrations" in result.output


def test_cli_help_is_comprehensive(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Persistent project-scoped Python REPL for agent workflows." in result.output
    assert "append-only notebook" in result.output
    assert "agentnb start --json" in result.output
    assert "--auto-install" in result.output
    assert "doctor --fix" in result.output


def test_cli_json_response_includes_suggestions(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["status", "--project", str(project_dir), "--json"])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["status"] == "ok"
    assert payload["command"] == "status"
    assert payload["suggestions"]


def test_cli_human_output_shows_suggestions(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["status", "--project", str(project_dir)])
    assert result.exit_code == 0
    assert "Kernel is not running." in result.output
    assert "Next:" in result.output


def test_cli_agent_preset_enables_json_and_suppresses_suggestions(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(main, ["--agent", "status", "--project", str(project_dir)])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["status"] == "ok"
    assert payload["command"] == "status"
    assert payload["suggestions"] == []


def test_cli_no_suggestions_strips_suggestions_from_json(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(
        main, ["--no-suggestions", "status", "--project", str(project_dir), "--json"]
    )
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["suggestions"] == []


def test_cli_env_format_json_applies_without_per_command_flag(
    cli_runner: CliRunner, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTNB_FORMAT", "json")

    result = cli_runner.invoke(main, ["status", "--project", str(project_dir)])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["command"] == "status"


def test_cli_exec_result_only_returns_selected_text(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    exec_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--result-only", "1 + 1"],
    )
    assert exec_res.exit_code == 0
    assert exec_res.output.strip() == "2"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_vars_includes_types_by_default(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "value = 42"])

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    assert vars_res.exit_code == 0

    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0]["name"] == "value"
    assert payload["data"]["vars"][0]["type"] == "int"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_vars_hides_routines_and_compacts_container_values(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(
        main,
        [
            "exec",
            "--project",
            str(project_dir),
            "--json",
            (
                "from urllib.request import urlopen\n"
                "from urllib.parse import urlencode\n"
                "posts = [{'id': 1, 'title': 'hello', 'body': 'world'}]\n"
                "query = {'postId': 1, '_limit': 2}"
            ),
        ],
    )

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    assert vars_res.exit_code == 0

    payload = _payload(vars_res.output)
    names = {item["name"] for item in payload["data"]["vars"]}
    assert "urlopen" not in names
    assert "urlencode" not in names
    assert payload["data"]["vars"][0]["repr"] == "list len=1 item_keys=id, title, body"
    assert payload["data"]["vars"][1]["repr"] == "dict len=2 keys=postId, _limit"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_vars_no_types_hides_types(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "value = 42"])

    vars_res = cli_runner.invoke(
        main,
        ["vars", "--project", str(project_dir), "--no-types", "--json"],
    )
    assert vars_res.exit_code == 0

    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0] == {"name": "value", "repr": "42"}

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_history_latest_returns_only_most_recent_entry(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 + 1"])
    cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "x = 2\nx + 2"])

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert len(payload["data"]["entries"]) == 1
    assert payload["data"]["entries"][0]["command_type"] == "exec"
    assert payload["data"]["entries"][0]["label"] == "exec x = 2 x + 2"
    assert payload["data"]["entries"][0]["kind"] == "user_command"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_history_hides_helper_code_by_default(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    _write_module(
        project_dir,
        "localmod",
        """
def greet() -> str:
    return "v1"
""".lstrip(),
    )
    cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--json", "value = 42\nimport localmod"],
    )
    cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    cli_runner.invoke(main, ["inspect", "--project", str(project_dir), "--json", "value"])
    cli_runner.invoke(main, ["reload", "--project", str(project_dir), "--json", "localmod"])

    history_res = cli_runner.invoke(main, ["history", "--project", str(project_dir), "--json"])
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entries = payload["data"]["entries"]
    assert [entry["command_type"] for entry in entries] == ["exec", "vars", "inspect", "reload"]
    assert all(entry["kind"] == "user_command" for entry in entries)
    assert [entry["label"] for entry in entries[1:]] == ["vars", "inspect value", "reload localmod"]
    assert not any("get_ipython" in str(entry.get("code")) for entry in entries)

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_history_all_includes_internal_helper_entries(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "value = 42"])
    cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--all", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entries = payload["data"]["entries"]
    assert len(entries) == 4
    assert [entry["kind"] for entry in entries] == [
        "kernel_execution",
        "user_command",
        "kernel_execution",
        "user_command",
    ]
    assert entries[-2]["command_type"] == "vars"
    assert entries[-2]["user_visible"] is False
    assert "code" not in entries[-2]

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_history_errors_filters_semantic_failures(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    inspect_res = cli_runner.invoke(
        main,
        ["inspect", "--project", str(project_dir), "--json", "missing_name"],
    )
    assert inspect_res.exit_code == 1

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--errors", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entries = payload["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["label"] == "inspect missing_name"
    assert entries[0]["kind"] == "user_command"
    assert entries[0]["status"] == "error"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_history_last_limits_visible_entries(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    _write_module(
        project_dir,
        "localmod",
        """
def greet() -> str:
    return "v1"
""".lstrip(),
    )
    cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--json", "value = 42\nimport localmod"],
    )
    cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    cli_runner.invoke(main, ["reload", "--project", str(project_dir), "--json", "localmod"])

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--last", "2", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert [entry["command_type"] for entry in payload["data"]["entries"]] == ["vars", "reload"]

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_reset_is_recorded_as_visible_history_entry(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(main, ["reset", "--project", str(project_dir), "--json"])

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entry = payload["data"]["entries"][0]
    assert entry["command_type"] == "reset"
    assert entry["label"] == "reset"
    assert entry["kind"] == "user_command"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_reload_without_module_reloads_project_local_imports(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    _write_module(
        project_dir,
        "localmod",
        """
def greet() -> str:
    return "v1"
""".lstrip(),
    )
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(
        main,
        [
            "exec",
            "--project",
            str(project_dir),
            "--json",
            "from localmod import greet\nimport localmod\nimport math",
        ],
    )
    _write_module(
        project_dir,
        "localmod",
        """
def greet() -> str:
    return "v2"
""".lstrip(),
    )

    before_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--json", "(greet(), localmod.greet())"],
    )
    assert before_res.exit_code == 0
    assert _payload(before_res.output)["data"]["result"] == "('v1', 'v1')"

    reload_res = cli_runner.invoke(main, ["reload", "--project", str(project_dir), "--json"])
    assert reload_res.exit_code == 0

    reload_payload = _payload(reload_res.output)
    assert reload_payload["data"]["mode"] == "project"
    assert reload_payload["data"]["requested_module"] is None
    assert reload_payload["data"]["reloaded_modules"] == ["localmod"]
    assert "greet" in reload_payload["data"]["rebound_names"]
    assert reload_payload["data"]["excluded_module_count"] > 0
    assert reload_payload["data"]["skipped_modules"] == []

    after_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--json", "(greet(), localmod.greet())"],
    )
    assert after_res.exit_code == 0
    assert _payload(after_res.output)["data"]["result"] == "('v2', 'v2')"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_vars_compacts_dataframe_repr(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(
        main,
        [
            "exec",
            "--project",
            str(project_dir),
            "--json",
            (
                "class FakeFrame:\n"
                "    shape = (10, 3)\n"
                "    columns = ['a', 'b', 'c']\n"
                "frame = FakeFrame()"
            ),
        ],
    )

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0]["repr"] == "DataFrame shape=(10, 3) columns=a, b, c"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_inspect_compacts_dataframe_payload(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    exec_res = cli_runner.invoke(
        main,
        [
            "exec",
            "--project",
            str(project_dir),
            "--json",
            """
class _DTypes:
    def __init__(self, mapping):
        self._mapping = mapping

    def astype(self, _type_name):
        return self

    def to_dict(self):
        return self._mapping


class _NullCounts:
    def __init__(self, mapping):
        self._mapping = mapping

    def sum(self):
        return self

    def to_dict(self):
        return self._mapping


class _HeadRows:
    def __init__(self, rows):
        self._rows = rows

    def reset_index(self):
        return self

    def to_dict(self, orient="records"):
        assert orient == "records"
        return self._rows


class DataFrameLike:
    shape = (4, 2)
    columns = ["a", "b"]
    dtypes = _DTypes({"a": "int64", "b": "int64"})

    def head(self, n):
        return _HeadRows(
            [
                {"a": 1, "b": 5},
                {"a": 2, "b": 6},
                {"a": 3, "b": 7},
                {"a": 4, "b": 8},
            ][:n]
        )

    def isna(self):
        return _NullCounts({"a": 0, "b": 0})


df = DataFrameLike()
""",
        ],
    )
    assert exec_res.exit_code == 0

    inspect_res = cli_runner.invoke(
        main,
        ["inspect", "--project", str(project_dir), "--json", "df"],
    )
    assert inspect_res.exit_code == 0
    payload = _payload(inspect_res.output)
    inspect_payload = payload["data"]["inspect"]
    assert "repr" not in inspect_payload
    assert "members" not in inspect_payload
    assert len(inspect_payload["preview"]["head"]) == 3

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_inspect_compacts_sequence_payload(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(
        main,
        [
            "exec",
            "--project",
            str(project_dir),
            "--json",
            (
                "posts = [\n"
                "    {'id': 1, 'title': 'a', 'body': 'alpha'},\n"
                "    {'id': 2, 'title': 'b', 'body': 'beta'},\n"
                "    {'id': 3, 'title': 'c', 'body': 'gamma'},\n"
                "]"
            ),
        ],
    )

    inspect_res = cli_runner.invoke(
        main,
        ["inspect", "--project", str(project_dir), "--json", "posts"],
    )
    assert inspect_res.exit_code == 0

    payload = _payload(inspect_res.output)
    inspect_payload = payload["data"]["inspect"]
    assert inspect_payload["preview"]["kind"] == "sequence-like"
    assert inspect_payload["preview"]["length"] == 3
    assert inspect_payload["preview"]["item_type"] == "dict"
    assert inspect_payload["preview"]["sample_keys"] == ["id", "title", "body"]
    assert len(inspect_payload["preview"]["sample"]) == 3
    assert "repr" not in inspect_payload
    assert "members" not in inspect_payload

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_history_exec_label_shortens_urls(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    cli_runner.invoke(
        main,
        [
            "exec",
            "--project",
            str(project_dir),
            "--json",
            (
                "url = 'https://jsonplaceholder.typicode.com/comments?"
                "postId=1&_limit=2&expand=author&include=metadata'\n"
                "url"
            ),
        ],
    )

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    label = payload["data"]["entries"][0]["label"]
    assert "jsonplaceholder.typicode.com" in label
    assert "metadata" not in label
    assert len(label) <= 69

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_history_last_rejects_latest_combination(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--last", "2"],
    )
    assert result.exit_code != 0
    assert "Use either --latest or --last" in result.output
