from __future__ import annotations

import json
import sys
from io import BytesIO, TextIOWrapper
from pathlib import Path

import pytest

from steadlith.cli import main
from steadlith.config import load_config
from steadlith.errors import ExitCode
from steadlith.index.service import verify_index
from steadlith.store import Cache


def test_index_rejects_cache_manifest_collision_before_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    config.write_text(
        '[store]\ncache="state.sqlite3.manifest.json"\n[index]\ndatabase="state.sqlite3"\n',
        encoding="utf-8",
    )
    source = tmp_path / "source.txt"
    source.write_text("alpha beta gamma", encoding="utf-8")
    original = {path.name: path.read_bytes() for path in tmp_path.iterdir()}

    assert main(["index", str(source), "-c", str(config), "--json"]) == ExitCode.CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ConfigError"
    assert "overlap" in payload["error"]
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == original


def test_invalid_utf8_config_reports_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    config.write_bytes(b"# invalid UTF-8: \xff\n")
    assert main(["plan", "-c", str(config), "--json"]) == ExitCode.CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ConfigError"
    assert "UTF-8" in payload["error"]


def test_init_status_and_verify_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    capsys.readouterr()
    assert main(["status", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {
        "active_chunks",
        "corpus_root",
        "documents",
        "generation",
        "hard_cut_rate",
        "hard_cuts",
        "model_id",
        "params_hash",
        "tombstoned_chunks",
        "total_boundaries",
    }
    assert payload["corpus_root"] is None
    assert payload["generation"] == 0
    assert payload["documents"] == 0
    assert not (tmp_path / ".steadlith" / "index.sqlite3").exists()
    assert main(["verify", "--config", str(config), "--json"]) == ExitCode.VERIFICATION_MISMATCH


def test_status_rejects_corrupt_index_without_mutating_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    database = tmp_path / ".steadlith" / "index.sqlite3"
    database.parent.mkdir()
    original = b"not a sqlite database"
    database.write_bytes(original)
    capsys.readouterr()

    assert main(["status", "--config", str(config), "--json"]) == ExitCode.BACKEND_OR_PROVIDER_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "BackendError"
    assert database.read_bytes() == original


def test_init_json_is_machine_readable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "steadlith.toml"

    assert main(["init", "--config", str(config), "--json"]) == ExitCode.SUCCESS

    payload = json.loads(capsys.readouterr().out)
    assert payload["created"] == str(config.resolve())
    assert payload["backend"] == "sqlite"
    assert payload["next"] == "review the sources table, then run steadlith plan"


@pytest.mark.parametrize("benchmark", ["churn", "retrieval"])
def test_benchmark_unknown_strategy_lists_valid_choices(
    benchmark: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["measure", benchmark, "--strategy", "missing", "--json"]) == ExitCode.CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == (
        "Unknown benchmark strategy: missing. Choose from: "
        "cdc-rabin, cdc-rabin+snap, fixed, recursive, semantic-lexical-proxy"
    )


def test_human_output_survives_a_legacy_windows_code_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = BytesIO()
    output = TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", output)
    config = tmp_path / "steadlith-≥.toml"

    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    output.flush()

    rendered = raw.getvalue().decode("cp1252")
    assert "steadlith-\\u2265.toml" in rendered


def test_missing_config_uses_config_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.toml"
    assert main(["plan", "--config", str(missing), "--json"]) == ExitCode.CONFIG_ERROR

    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ConfigError"
    assert "Configuration not found" in payload["error"]


def test_json_errors_remain_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing.toml"
    assert main(["query", "hello", "--config", str(missing), "--json"]) == ExitCode.CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ConfigError"
    assert payload["exit_code"] == ExitCode.CONFIG_ERROR
    assert "Configuration not found" in payload["error"]


@pytest.mark.parametrize("cutoff", ["2026-08-15", "zzz"])
def test_compact_rejects_ambiguous_or_invalid_cutoff(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], cutoff: str
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    capsys.readouterr()

    assert (
        main(["compact", "--before", cutoff, "--config", str(config), "--json"])
        == ExitCode.CONFIG_ERROR
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ConfigError"
    assert "--before" in payload["error"]


def test_migration_apply_requires_an_existing_index(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS

    assert (
        main(
            [
                "migrate",
                "--embedding-model",
                "replacement-model",
                "--apply",
                "--config",
                str(config),
                "--json",
            ]
        )
        == ExitCode.CONFIG_ERROR
    )
    payload = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert payload["error_type"] == "ConfigError"
    assert "existing index" in payload["error"]
    assert not (tmp_path / ".steadlith/index.sqlite3").exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["measure", "churn", "--price", "-1", "--json"], "--price"),
        (["measure", "churn", "--price", "nan", "--json"], "--price"),
        (["measure", "retrieval", "-k", "0", "--json"], "-k"),
    ],
)
def test_measurement_argument_errors_are_typed(
    arguments: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(arguments) == ExitCode.CONFIG_ERROR
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ConfigError"
    assert message in payload["error"]


def test_index_requires_explicit_deletion_and_empty_corpus_confirmation(tmp_path: Path) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'include = ["docs/**/*.md", "README.md"]', 'include = ["docs/*.md"]'
        ),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    source = docs / "guide.md"
    source.write_text(" ".join(f"word-{index}" for index in range(200)), encoding="utf-8")
    assert main(["index", "--config", str(config), "--json"]) == ExitCode.SUCCESS

    source.unlink()
    assert main(["index", "--config", str(config), "--json"]) == ExitCode.CONFIG_ERROR
    assert (
        main(["index", "--allow-delete", "--config", str(config), "--json"])
        == ExitCode.CONFIG_ERROR
    )
    assert (
        main(
            [
                "index",
                "--allow-delete",
                "--allow-empty",
                "--config",
                str(config),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )


def test_non_demo_provider_requires_explicit_network_confirmation(tmp_path: Path) -> None:
    config = tmp_path / "steadlith.toml"
    config.write_text(
        '[embedding]\nprovider = "openai"\nmodel = "text-embedding-test"\n',
        encoding="utf-8",
    )
    assert main(["index", "--config", str(config), "--json"]) == ExitCode.CONFIG_ERROR
    assert main(["query", "hello", "--config", str(config), "--json"]) == ExitCode.CONFIG_ERROR


@pytest.mark.parametrize("command", [["index"], ["query", "hello"]])
def test_network_gate_names_custom_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: list[str],
) -> None:
    config = tmp_path / "custom.toml"
    config.write_text(
        '[embedding]\nprovider = "openai"\nmodel = "text-embedding-test"\n',
        encoding="utf-8",
    )

    assert main([*command, "--config", str(config), "--json"]) == ExitCode.CONFIG_ERROR

    payload = json.loads(capsys.readouterr().out)
    assert str(config) in payload["error"]


def test_cache_cli_round_trip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    capsys.readouterr()
    with Cache(tmp_path / ".steadlith" / "cache.sqlite3") as cache:
        cache.put("chunk-a", "model", "params", (1.0, 0.0), token_count=2)
        cache.put("chunk-b", "model", "params", (0.0, 1.0), token_count=3)

    assert main(["cache", "stats", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    assert json.loads(capsys.readouterr().out)["entries"] == 2

    export = tmp_path / "cache.jsonl"
    assert (
        main(["cache", "export", str(export), "--config", str(config), "--json"])
        == ExitCode.SUCCESS
    )
    assert json.loads(capsys.readouterr().out)["exported"] == 2
    assert (
        main(["cache", "export", str(export), "--config", str(config), "--json"])
        == ExitCode.BACKEND_OR_PROVIDER_ERROR
    )
    assert "Refusing to overwrite" in json.loads(capsys.readouterr().out)["error"]
    assert (
        main(["cache", "export", str(export), "--force", "--config", str(config), "--json"])
        == ExitCode.SUCCESS
    )
    capsys.readouterr()

    assert (
        main(["cache", "prune", "--max-entries", "1", "--config", str(config), "--json"])
        == ExitCode.SUCCESS
    )
    assert json.loads(capsys.readouterr().out)["removed"] == 1
    assert (
        main(["cache", "import", str(export), "--config", str(config), "--json"])
        == ExitCode.CONFIG_ERROR
    )
    assert "--trust-source" in json.loads(capsys.readouterr().out)["error"]
    assert (
        main(
            [
                "cache",
                "import",
                str(export),
                "--trust-source",
                "--config",
                str(config),
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )
    assert json.loads(capsys.readouterr().out)["imported"] == 2
    with Cache(tmp_path / ".steadlith" / "cache.sqlite3", readonly=True) as cache:
        assert cache.stats().entries == 2


@pytest.mark.parametrize(
    "target",
    [
        "steadlith.toml",
        "steadlith.toml.migration.json",
        ".steadlith/index.sqlite3",
        ".steadlith/index.sqlite3-wal",
        ".steadlith/index.sqlite3-shm",
        ".steadlith/index.sqlite3-journal",
        ".steadlith/index.sqlite3.manifest.json",
        ".steadlith/index.sqlite3.migrations/export.jsonl",
        ".steadlith/cache.sqlite3",
        ".steadlith/cache.sqlite3-wal",
        ".steadlith/cache.sqlite3-shm",
        ".steadlith/cache.sqlite3-journal",
        "receipt",
    ],
)
def test_cache_export_cannot_replace_project_state_even_with_force(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], target: str
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "-c", str(config), "--json"]) == ExitCode.SUCCESS
    (tmp_path / "README.md").write_text("synthetic retrieval evidence", encoding="utf-8")
    assert main(["index", "-c", str(config), "--json"]) == ExitCode.SUCCESS
    if target == "receipt":
        assert (
            main(
                [
                    "migrate",
                    "--embedding-model",
                    "export-test-model",
                    "--apply",
                    "--allow-delete",
                    "-c",
                    str(config),
                    "--json",
                ]
            )
            == ExitCode.SUCCESS
        )
        destination = next((tmp_path / ".steadlith/index.sqlite3.migrations").glob("*.json"))
    else:
        destination = tmp_path / target
    original = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    capsys.readouterr()
    if target in {
        ".steadlith/cache.sqlite3",
        ".steadlith/cache.sqlite3-wal",
        ".steadlith/cache.sqlite3-shm",
    }:
        expected_code = ExitCode.BACKEND_OR_PROVIDER_ERROR
        expected_message = "live cache or its sidecars"
    else:
        expected_code = ExitCode.CONFIG_ERROR
        expected_message = (
            "configured index" if target == ".steadlith/index.sqlite3" else "managed project state"
        )

    assert (
        main(["cache", "export", str(destination), "--force", "-c", str(config), "--json"])
        == expected_code
    )

    error = json.loads(capsys.readouterr().out)
    assert error["error_type"] == (
        "ConfigError" if expected_code == ExitCode.CONFIG_ERROR else "BackendError"
    )
    assert expected_message in error["error"]
    assert {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    } == original
    assert verify_index(load_config(config)) == (True, ())


def test_measure_churn_cli_forwards_filters(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "measure",
                "churn",
                "--strategy",
                "fixed",
                "--corpus",
                "field-guide",
                "--edit",
                "append",
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert (result["strategy"], result["corpus_name"], result["operation"]) == (
        "fixed",
        "field-guide",
        "append",
    )


def test_measure_retrieval_cli_forwards_filters_and_k(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        main(
            [
                "measure",
                "retrieval",
                "--strategy",
                "fixed",
                "--corpus",
                "field-guide",
                "--scoring",
                "hash-embedding",
                "-k",
                "1",
                "--json",
            ]
        )
        == ExitCode.SUCCESS
    )

    payload = json.loads(capsys.readouterr().out)
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["strategy"] == "fixed"
    assert result["scoring_method"] == "hash-embedding"
    assert result["k"] == 1


def test_measure_cli_human_rendering(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        main(
            [
                "measure",
                "churn",
                "--strategy",
                "fixed",
                "--corpus",
                "field-guide",
                "--edit",
                "append",
            ]
        )
        == ExitCode.SUCCESS
    )
    churn_output = capsys.readouterr().out
    assert "Churn benchmark" in churn_output
    assert "field-guide" in churn_output
    assert "append" in churn_output

    assert (
        main(
            [
                "measure",
                "retrieval",
                "--strategy",
                "fixed",
                "--corpus",
                "field-guide",
                "--scoring",
                "hash-embedding",
                "-k",
                "1",
            ]
        )
        == ExitCode.SUCCESS
    )
    retrieval_output = capsys.readouterr().out
    assert "Retrieval benchmark" in retrieval_output
    assert "hash-embedding" in retrieval_output


def test_explicit_plan_index_and_healthy_verify(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    source = tmp_path / "manual.txt"
    source.write_text("an explicitly selected source document", encoding="utf-8")
    assert main(["init", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    capsys.readouterr()

    assert main(["plan", str(source), "--config", str(config), "--json"]) == ExitCode.SUCCESS
    plan = json.loads(capsys.readouterr().out)
    assert plan["counts"]["add"] == 1
    assert plan["new_chunks"] == 1

    assert main(["index", str(source), "--config", str(config), "--json"]) == ExitCode.SUCCESS
    applied = json.loads(capsys.readouterr().out)
    assert applied["active_chunks"] == 1

    assert main(["verify", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    assert json.loads(capsys.readouterr().out) == {"problems": [], "valid": True}


def test_query_limit_controls_machine_readable_active_results(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'include = ["docs/**/*.md", "README.md"]', 'include = ["docs/*.md"]'
        ),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    for index in range(3):
        (docs / f"guide-{index}.md").write_text(
            f"guide {index} contains quartz needle evidence",
            encoding="utf-8",
        )
    assert main(["index", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    capsys.readouterr()

    for limit in (1, 2):
        assert (
            main(
                [
                    "query",
                    "quartz needle",
                    "--limit",
                    str(limit),
                    "--config",
                    str(config),
                    "--json",
                ]
            )
            == ExitCode.SUCCESS
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["query"] == "quartz needle"
        assert payload["count"] == limit
        assert len(payload["matches"]) == limit
        assert all("quartz needle" in match["text"] for match in payload["matches"])


def test_query_argument_errors_are_typed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    capsys.readouterr()
    assert (
        main(["query", "hello", "--limit", "0", "--config", str(config), "--json"])
        == ExitCode.CONFIG_ERROR
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ConfigError"
    assert "positive" in payload["error"]


def test_status_reads_committed_state_without_replanning_sources(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "steadlith.toml"
    assert main(["init", "--config", str(config)]) == ExitCode.SUCCESS
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'include = ["docs/**/*.md", "README.md"]', 'include = ["docs/*.md"]'
        ),
        encoding="utf-8",
    )
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(" ".join(f"word-{i}" for i in range(200)), encoding="utf-8")
    assert main(["index", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'model = "steadlith-hash-256-v1"', 'model = "steadlith-hash-256-v2"'
        ),
        encoding="utf-8",
    )
    (docs / "guide.md").write_bytes(b"invalid utf-8: \xff")
    capsys.readouterr()

    assert main(["status", "--config", str(config), "--json"]) == ExitCode.SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["documents"] == 1
    assert payload["active_chunks"] > 0
    assert payload["model_id"] == "hash:steadlith-hash-256-v1"

    assert main(["plan", "--config", str(config), "--json"]) == ExitCode.PLAN_OR_APPLY_FAILURE
    error = json.loads(capsys.readouterr().out)
    assert "not valid UTF-8" in error["error"]
