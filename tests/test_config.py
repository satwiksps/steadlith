from __future__ import annotations

from pathlib import Path

import pytest

from steadlith.config import ChunkerConfig, EmbeddingConfig, load_config, write_default_config
from steadlith.errors import ConfigError


def test_default_config_round_trip(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "steadlith.toml")
    config = load_config(path)
    assert config.chunker.strategy == "cdc-rabin"
    assert config.embedding.provider == "hash"
    assert config.resolve(config.store.cache) == (tmp_path / ".steadlith/cache.sqlite3").resolve()


def test_default_config_is_not_overwritten(tmp_path: Path) -> None:
    path = write_default_config(tmp_path / "steadlith.toml")
    with pytest.raises(ConfigError, match="Refusing to overwrite"):
        write_default_config(path)


def test_unknown_chunking_strategy_lists_accepted_values() -> None:
    with pytest.raises(ConfigError) as exc_info:
        ChunkerConfig(strategy="typo").validate()

    assert str(exc_info.value) == (
        "Unknown chunking strategy: 'typo'. Accepted values: "
        "cdc-rabin, cdc-rabin+snap, fixed, recursive, semantic"
    )


def test_unknown_embedding_provider_lists_accepted_values() -> None:
    with pytest.raises(ConfigError) as exc_info:
        EmbeddingConfig(provider="typo").validate()

    assert str(exc_info.value) == (
        "Unknown embedding provider: 'typo'. Accepted values: hash, openai, sentence-transformers"
    )


def test_unknown_config_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "steadlith.toml"
    path.write_text("[chunker]\nmagic = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown key"):
        load_config(path)


def test_unimplemented_index_namespace_settings_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "steadlith.toml"
    path.write_text('[index]\ndatabase = "index.sqlite3"\ncollection = "docs"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="Unknown key.*collection"):
        load_config(path)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ('[sources]\ninclude = "docs/*.md"\n', "array of strings"),
        ('[chunker]\nwindow_words = "48"\n', "must be integers"),
        ('[project]\nname = "typo"\n', "Unknown top-level"),
        ('[sources]\ninclude = ["../secret.md"]\n', "must stay below"),
        ('[store]\ncache = "../shared.sqlite3"\n', "must stay below"),
        (
            '[store]\ncache = ".steadlith/state.sqlite3"\n[index]\ndatabase = ".steadlith/state.sqlite3"\n',
            "must use different files",
        ),
        ("[embedding]\ndimensions = 65537\n", "must be between"),
        ("[embedding]\nprice_per_million_tokens = nan\n", "must be finite"),
        (
            '[embedding]\nprovider = "openai"\napi_key_env = "AWS_SECRET_ACCESS_KEY"\n',
            "only reads OPENAI_API_KEY",
        ),
        (
            '[embedding]\nprovider = "openai"\nbase_url = "https://attacker.invalid"\n',
            "Custom OpenAI base_url endpoints are not supported",
        ),
    ],
)
def test_invalid_config_types_and_tables_are_friendly(
    tmp_path: Path, payload: str, message: str
) -> None:
    path = tmp_path / "steadlith.toml"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_config(path)


@pytest.mark.parametrize(
    ("cache", "database", "filename"),
    [
        ("state.sqlite3.manifest.json", "state.sqlite3", "steadlith.toml"),
        ("state.sqlite3-wal", "state.sqlite3", "steadlith.toml"),
        ("state.sqlite3-shm", "state.sqlite3", "steadlith.toml"),
        ("state.sqlite3-journal", "state.sqlite3", "steadlith.toml"),
        ("cache.sqlite3", "cache.sqlite3-wal", "steadlith.toml"),
        ("state.sqlite3.migrations/cache.sqlite3", "state.sqlite3", "steadlith.toml"),
        ("state/cache.sqlite3", "state", "steadlith.toml"),
        ("cache.sqlite3", "state.sqlite3", "cache.sqlite3"),
        ("cache.sqlite3", "state.sqlite3", "state.sqlite3.manifest.json"),
        ("steadlith.toml.migration.json", "state.sqlite3", "steadlith.toml"),
    ],
)
def test_config_rejects_overlapping_state_paths(
    tmp_path: Path, cache: str, database: str, filename: str
) -> None:
    path = tmp_path / filename
    payload = f'[store]\ncache = "{cache}"\n[index]\ndatabase = "{database}"\n'
    path.write_text(payload, encoding="utf-8")

    with pytest.raises(ConfigError, match="overlap"):
        load_config(path)

    assert path.read_text(encoding="utf-8") == payload
    assert sorted(tmp_path.iterdir()) == [path]


def test_config_rejects_invalid_utf8_with_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "steadlith.toml"
    path.write_bytes(b"# invalid UTF-8: \xff\n")

    with pytest.raises(ConfigError, match="UTF-8"):
        load_config(path)
