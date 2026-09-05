"""Typed, deliberately small TOML configuration support."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

try:  # pragma: no cover - selected by interpreter version
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib

from steadlith._legacy_wire import (
    LEGACY_CACHE_PATH,
    LEGACY_CONFIG_FILENAME,
    LEGACY_HASH_MODEL,
    LEGACY_INDEX_PATH,
    LEGACY_SOURCE_EXCLUDE,
    LEGACY_STATE_DIRECTORY,
)
from steadlith.errors import ConfigError

MAX_VECTOR_DIMENSIONS = 65_536
DEFAULT_CONFIG_FILENAME = "steadlith.toml"
DEFAULT_STATE_DIRECTORY = ".steadlith"
DEFAULT_HASH_MODEL = "steadlith-hash-256-v1"


def pending_migration_path(path: str | Path) -> Path:
    """Return the durable migration journal path associated with a config file."""

    config_path = Path(path).expanduser().resolve()
    return config_path.with_name(f"{config_path.name}.migration.json")


DEFAULT_CONFIG = """\
# Steadlith keeps state below .steadlith/. Commit steadlith.toml; ignore .steadlith/.
[chunker]
strategy = "cdc-rabin"
window_words = 48
min_tokens = 180
max_tokens = 640
snap_window_words = 24
primary_mask_bits = 8
backup_mask_bits = 6

# The deterministic hash provider provides offline lexical retrieval. Select a
# learned provider when queries need semantic similarity or synonym matching.
[embedding]
provider = "hash"
model = "steadlith-hash-256-v1"
dimensions = 256
batch_size = 64
price_per_million_tokens = 0.0

[store]
cache = ".steadlith/cache.sqlite3"

[index]
backend = "sqlite"
database = ".steadlith/index.sqlite3"

[sources]
include = ["docs/**/*.md", "README.md"]
exclude = ["**/drafts/**", "**/.git/**", "**/.steadlith/**"]
"""


@dataclass(frozen=True)
class ChunkerConfig:
    strategy: str = "cdc-rabin"
    window_words: int = 48
    min_tokens: int = 180
    max_tokens: int = 640
    snap_window_words: int = 24
    primary_mask_bits: int = 8
    backup_mask_bits: int = 6

    def validate(self) -> None:
        if not isinstance(self.strategy, str):
            raise ConfigError("chunker.strategy must be a string")
        accepted_strategies = (
            "cdc-rabin",
            "cdc-rabin+snap",
            "fixed",
            "recursive",
            "semantic",
        )
        if self.strategy not in accepted_strategies:
            accepted = ", ".join(accepted_strategies)
            raise ConfigError(
                f"Unknown chunking strategy: {self.strategy!r}. Accepted values: {accepted}"
            )
        integer_fields = {
            "window_words": self.window_words,
            "min_tokens": self.min_tokens,
            "max_tokens": self.max_tokens,
            "snap_window_words": self.snap_window_words,
            "primary_mask_bits": self.primary_mask_bits,
            "backup_mask_bits": self.backup_mask_bits,
        }
        invalid = [name for name, value in integer_fields.items() if type(value) is not int]
        if invalid:
            raise ConfigError(f"chunker integer field(s) must be integers: {', '.join(invalid)}")
        if self.window_words < 2:
            raise ConfigError("chunker.window_words must be at least 2")
        if not 0 < self.min_tokens < self.max_tokens:
            raise ConfigError("chunker sizes must satisfy 0 < min_tokens < max_tokens")
        if self.snap_window_words < 0:
            raise ConfigError("chunker.snap_window_words cannot be negative")
        if not 1 <= self.backup_mask_bits < self.primary_mask_bits <= 63:
            raise ConfigError("chunker mask bits must satisfy 1 <= backup < primary <= 63")


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str = "hash"
    model: str = DEFAULT_HASH_MODEL
    dimensions: int = 256
    batch_size: int = 64
    price_per_million_tokens: float | None = None
    api_key_env: str | None = None
    base_url: str | None = None

    def validate(self) -> None:
        if not isinstance(self.provider, str):
            raise ConfigError("embedding.provider must be a string")
        accepted_providers = ("hash", "openai", "sentence-transformers")
        if self.provider not in accepted_providers:
            accepted = ", ".join(accepted_providers)
            raise ConfigError(
                f"Unknown embedding provider: {self.provider!r}. Accepted values: {accepted}"
            )
        if not isinstance(self.model, str):
            raise ConfigError("embedding.model must be a string")
        if not self.model.strip():
            raise ConfigError("embedding.model cannot be empty")
        if type(self.dimensions) is not int:
            raise ConfigError("embedding.dimensions must be an integer")
        if not 0 < self.dimensions <= MAX_VECTOR_DIMENSIONS:
            raise ConfigError(
                f"embedding.dimensions must be between 1 and {MAX_VECTOR_DIMENSIONS:,}"
            )
        if type(self.batch_size) is not int:
            raise ConfigError("embedding.batch_size must be an integer")
        if self.batch_size <= 0:
            raise ConfigError("embedding.batch_size must be positive")
        if self.price_per_million_tokens is not None:
            if isinstance(self.price_per_million_tokens, bool) or not isinstance(
                self.price_per_million_tokens, (int, float)
            ):
                raise ConfigError("embedding.price_per_million_tokens must be numeric")
            if not math.isfinite(self.price_per_million_tokens):
                raise ConfigError("embedding.price_per_million_tokens must be finite")
            if self.price_per_million_tokens < 0:
                raise ConfigError("embedding.price_per_million_tokens cannot be negative")
        if self.api_key_env is not None and not isinstance(self.api_key_env, str):
            raise ConfigError("embedding.api_key_env must be a string")
        if self.base_url is not None and not isinstance(self.base_url, str):
            raise ConfigError("embedding.base_url must be a string")
        if self.provider == "openai":
            if self.api_key_env not in {None, "OPENAI_API_KEY"}:
                raise ConfigError(
                    "The OpenAI adapter only reads OPENAI_API_KEY; custom secret "
                    "environment variables are not accepted from project config"
                )
            if self.base_url is not None:
                raise ConfigError("Custom OpenAI base_url endpoints are not supported")
        elif self.api_key_env is not None or self.base_url is not None:
            raise ConfigError(
                "embedding.api_key_env and embedding.base_url are only valid for OpenAI"
            )


@dataclass(frozen=True)
class StoreConfig:
    cache: str = ".steadlith/cache.sqlite3"

    def validate(self) -> None:
        if not isinstance(self.cache, str) or not self.cache.strip():
            raise ConfigError("store.cache must be a non-empty path string")


@dataclass(frozen=True)
class IndexConfig:
    backend: str = "sqlite"
    database: str = ".steadlith/index.sqlite3"

    def validate(self) -> None:
        if not isinstance(self.backend, str):
            raise ConfigError("index.backend must be a string")
        if self.backend != "sqlite":
            raise ConfigError(
                f"Backend {self.backend!r} is not installed; this build provides 'sqlite'"
            )
        if not isinstance(self.database, str) or not self.database.strip():
            raise ConfigError("index.database must be a non-empty path string")


@dataclass(frozen=True)
class SourcesConfig:
    include: tuple[str, ...] = ("docs/**/*.md", "README.md")
    exclude: tuple[str, ...] = ("**/drafts/**", "**/.git/**", "**/.steadlith/**")

    def validate(self) -> None:
        for name, patterns in {"include": self.include, "exclude": self.exclude}.items():
            if not isinstance(patterns, tuple) or any(
                not isinstance(pattern, str) or not pattern.strip() for pattern in patterns
            ):
                raise ConfigError(f"sources.{name} must be an array of non-empty strings")
            unsafe = [
                pattern
                for pattern in patterns
                if Path(pattern).is_absolute() or ".." in Path(pattern).parts
            ]
            if unsafe:
                raise ConfigError(
                    f"sources.{name} patterns must stay below the config directory: "
                    f"{', '.join(unsafe)}"
                )


@dataclass(frozen=True)
class SteadlithConfig:
    """Validated configuration plus the directory paths are resolved against."""

    chunker: ChunkerConfig = field(default_factory=ChunkerConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    store: StoreConfig = field(default_factory=StoreConfig)
    index: IndexConfig = field(default_factory=IndexConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    base_dir: Path = field(default_factory=Path.cwd, compare=False, repr=False)
    config_path: Path | None = field(default=None, compare=False, repr=False)

    def validate(self) -> SteadlithConfig:
        self.chunker.validate()
        self.embedding.validate()
        self.store.validate()
        self.index.validate()
        self.sources.validate()
        base = self.base_dir.expanduser().resolve()
        state_paths: dict[str, Path] = {}
        for label, value in {
            "store.cache": self.store.cache,
            "index.database": self.index.database,
        }.items():
            resolved = self.resolve(value)
            state_paths[label] = resolved
            try:
                resolved.relative_to(base)
            except ValueError as exc:
                raise ConfigError(
                    f"{label} must stay below the configuration directory: {resolved}"
                ) from exc
        if state_paths["store.cache"] == state_paths["index.database"]:
            raise ConfigError("store.cache and index.database must use different files")
        # SQLite sidecars and derived index files share the state namespace.
        # A collision can replace a live database when the manifest is published.
        reserved = dict(state_paths)
        for label, path in state_paths.items():
            for suffix in ("-wal", "-shm", "-journal"):
                reserved[f"{label} {suffix} sidecar"] = Path(f"{path}{suffix}").resolve()
        database = state_paths["index.database"]
        reserved["index manifest"] = Path(f"{database}.manifest.json").resolve()
        reserved["migration receipts"] = Path(f"{database}.migrations").resolve()
        if self.config_path is not None:
            reserved["configuration"] = self.config_path.expanduser().resolve()
            reserved["migration journal"] = pending_migration_path(self.config_path)
        paths = list(reserved.items())
        for index, (label, path) in enumerate(paths):
            for other_label, other in paths[index + 1 :]:
                if path == other or path in other.parents or other in path.parents:
                    raise ConfigError(
                        f"State paths overlap: {label} ({path}) and {other_label} ({other}). "
                        "Configure separate paths outside reserved state files and directories."
                    )
        return self

    def resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        return path.resolve()

    def with_chunker(self, **changes: Any) -> SteadlithConfig:
        return replace(self, chunker=replace(self.chunker, **changes)).validate()

    def with_embedding(self, **changes: Any) -> SteadlithConfig:
        return replace(self, embedding=replace(self.embedding, **changes)).validate()


def _table(data: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, Mapping):
        raise ConfigError(f"[{name}] must be a TOML table")
    return value


def _only_known(table: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise ConfigError(f"Unknown key(s) in [{name}]: {', '.join(unknown)}")


def _string_array(table: Mapping[str, Any], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = table.get(key, default)
    if not isinstance(value, list | tuple) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"sources.{key} must be an array of strings")
    return tuple(value)


def _config_from_mapping(
    raw: Mapping[str, Any], *, base_dir: Path, config_path: Path | None = None
) -> SteadlithConfig:
    if not isinstance(raw, Mapping):
        raise ConfigError("The configuration root must be a TOML table")
    unknown_tables = sorted(set(raw) - {"chunker", "embedding", "store", "index", "sources"})
    if unknown_tables:
        raise ConfigError(f"Unknown top-level table(s): {', '.join(unknown_tables)}")

    chunker_raw = _table(raw, "chunker")
    embedding_raw = _table(raw, "embedding")
    store_raw = _table(raw, "store")
    index_raw = _table(raw, "index")
    sources_raw = _table(raw, "sources")
    _only_known(
        chunker_raw,
        {
            "strategy",
            "window_words",
            "min_tokens",
            "max_tokens",
            "snap_window_words",
            "primary_mask_bits",
            "backup_mask_bits",
        },
        "chunker",
    )
    _only_known(
        embedding_raw,
        {
            "provider",
            "model",
            "dimensions",
            "batch_size",
            "price_per_million_tokens",
            "api_key_env",
            "base_url",
        },
        "embedding",
    )
    _only_known(store_raw, {"cache"}, "store")
    _only_known(index_raw, {"backend", "database"}, "index")
    _only_known(sources_raw, {"include", "exclude"}, "sources")

    try:
        source_defaults = SourcesConfig()
        sources = SourcesConfig(
            include=_string_array(sources_raw, "include", source_defaults.include),
            exclude=_string_array(sources_raw, "exclude", source_defaults.exclude),
        )
        config = SteadlithConfig(
            chunker=ChunkerConfig(**dict(chunker_raw)),
            embedding=EmbeddingConfig(**dict(embedding_raw)),
            store=StoreConfig(**dict(store_raw)),
            index=IndexConfig(**dict(index_raw)),
            sources=sources,
            base_dir=base_dir.expanduser().resolve(),
            config_path=config_path.expanduser().resolve() if config_path is not None else None,
        )
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid configuration value: {exc}") from exc
    try:
        return config.validate()
    except ConfigError:
        raise
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Invalid configuration value: {exc}") from exc


def _has_legacy_marker(raw: Mapping[str, Any], config_path: Path | None) -> bool:
    if config_path is not None and config_path.name.casefold() == LEGACY_CONFIG_FILENAME:
        return True
    embedding = raw.get("embedding", {})
    store = raw.get("store", {})
    index = raw.get("index", {})
    sources = raw.get("sources", {})
    if isinstance(embedding, Mapping) and embedding.get("model") == LEGACY_HASH_MODEL:
        return True
    for table, key in ((store, "cache"), (index, "database")):
        if isinstance(table, Mapping):
            value = table.get(key)
            if isinstance(value, str) and LEGACY_STATE_DIRECTORY in Path(value).parts:
                return True
    return bool(
        isinstance(sources, Mapping)
        and isinstance(sources.get("exclude"), list | tuple)
        and any(LEGACY_STATE_DIRECTORY in str(pattern) for pattern in sources["exclude"])
    )


def _apply_legacy_defaults(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    """Apply the defaults shipped by v0.2 to an explicitly legacy config."""

    result: dict[str, Any] = dict(raw)
    defaults: tuple[tuple[str, Mapping[str, Any]], ...] = (
        ("embedding", {"model": LEGACY_HASH_MODEL}),
        ("store", {"cache": LEGACY_CACHE_PATH}),
        ("index", {"database": LEGACY_INDEX_PATH}),
        ("sources", {"exclude": list(LEGACY_SOURCE_EXCLUDE)}),
    )
    for name, values in defaults:
        existing = result.get(name, {})
        if not isinstance(existing, Mapping):
            continue
        table = dict(existing)
        for key, value in values.items():
            table.setdefault(key, value)
        result[name] = table
    return result


def loads_config(
    text: str, *, base_dir: str | Path, config_path: str | Path | None = None
) -> SteadlithConfig:
    """Parse and validate TOML text without touching the filesystem."""

    if not isinstance(text, str):
        raise ConfigError("Configuration text must be a string")
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse configuration: {exc}") from exc
    parsed_path = Path(config_path) if config_path is not None else None
    if _has_legacy_marker(raw, parsed_path):
        raw = _apply_legacy_defaults(raw)
    return _config_from_mapping(
        raw,
        base_dir=Path(base_dir),
        config_path=parsed_path,
    )


def load_config(path: str | Path = DEFAULT_CONFIG_FILENAME) -> SteadlithConfig:
    """Load and validate a Steadlith configuration file."""

    config_path = Path(path).expanduser().resolve()
    journal = pending_migration_path(config_path)
    if journal.exists():
        raise ConfigError(
            f"A pending migration journal exists at {journal}. Run "
            f"'steadlith migrate --recover --config {config_path}' before using this config."
        )
    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        legacy_path = config_path.with_name(LEGACY_CONFIG_FILENAME)
        if config_path.name == DEFAULT_CONFIG_FILENAME and legacy_path.exists():
            raise ConfigError(
                f"Configuration not found: {config_path}. An earlier v0.2 config exists at "
                f"{legacy_path}. Adopt it without rebuilding state with "
                f"'steadlith adopt --from-config {legacy_path} --config {config_path}'."
            ) from exc
        raise ConfigError(
            f"Configuration not found: {config_path}. Run 'steadlith init' first."
        ) from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Configuration must be valid UTF-8: {config_path}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {config_path}: {exc}") from exc
    if _has_legacy_marker(raw, config_path):
        raw = _apply_legacy_defaults(raw)
    return _config_from_mapping(raw, base_dir=config_path.parent, config_path=config_path)


def write_default_config(
    path: str | Path = DEFAULT_CONFIG_FILENAME, *, force: bool = False
) -> Path:
    """Create a commented starter config without overwriting by default."""

    destination = Path(path).expanduser().resolve()
    journal = pending_migration_path(destination)
    if journal.exists():
        raise ConfigError(
            f"Refusing to replace config while a migration is pending at {journal}; "
            "run 'steadlith migrate --recover' first"
        )
    if destination.exists() and not force:
        raise ConfigError(f"Refusing to overwrite existing configuration: {destination}")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(DEFAULT_CONFIG, encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ConfigError(f"Could not write configuration {destination}: {exc}") from exc
    return destination


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def render_config(config: SteadlithConfig, *, adopted: bool = False) -> str:
    """Serialize a validated config to stable, human-readable TOML."""

    value = config.validate()
    lines = [
        (
            "# Steadlith configuration adopted from an earlier release. Existing state and "
            "model identity are preserved."
            if adopted
            else "# Steadlith configuration."
        ),
        "[chunker]",
        f"strategy = {_toml_string(value.chunker.strategy)}",
        f"window_words = {value.chunker.window_words}",
        f"min_tokens = {value.chunker.min_tokens}",
        f"max_tokens = {value.chunker.max_tokens}",
        f"snap_window_words = {value.chunker.snap_window_words}",
        f"primary_mask_bits = {value.chunker.primary_mask_bits}",
        f"backup_mask_bits = {value.chunker.backup_mask_bits}",
        "",
        "[embedding]",
        f"provider = {_toml_string(value.embedding.provider)}",
        f"model = {_toml_string(value.embedding.model)}",
        f"dimensions = {value.embedding.dimensions}",
        f"batch_size = {value.embedding.batch_size}",
    ]
    if value.embedding.price_per_million_tokens is not None:
        lines.append(
            f"price_per_million_tokens = {float(value.embedding.price_per_million_tokens)!r}"
        )
    if value.embedding.api_key_env is not None:
        lines.append(f"api_key_env = {_toml_string(value.embedding.api_key_env)}")
    if value.embedding.base_url is not None:
        lines.append(f"base_url = {_toml_string(value.embedding.base_url)}")
    lines.extend(
        (
            "",
            "[store]",
            f"cache = {_toml_string(value.store.cache)}",
            "",
            "[index]",
            f"backend = {_toml_string(value.index.backend)}",
            f"database = {_toml_string(value.index.database)}",
            "",
            "[sources]",
            f"include = {_toml_array(value.sources.include)}",
            f"exclude = {_toml_array(value.sources.exclude)}",
            "",
        )
    )
    return "\n".join(lines)


def adopt_legacy_config(
    source: str | Path = LEGACY_CONFIG_FILENAME,
    destination: str | Path = DEFAULT_CONFIG_FILENAME,
) -> Path:
    """Create a Steadlith config that safely reuses explicit v0.2 state."""

    source_path = Path(source).expanduser().resolve()
    destination_path = Path(destination).expanduser().resolve()
    if source_path == destination_path:
        raise ConfigError("Adoption source and destination must be different files")
    if source_path.parent != destination_path.parent:
        raise ConfigError(
            "Adoption source and destination must be in the same directory so relative "
            "source and state paths keep their meaning"
        )
    if destination_path.exists():
        raise ConfigError(f"Refusing to overwrite existing configuration: {destination_path}")
    destination_journal = pending_migration_path(destination_path)
    if destination_journal.exists():
        raise ConfigError(
            f"Refusing to adopt while a migration journal exists at {destination_journal}"
        )
    source_journal = pending_migration_path(source_path)
    if source_journal.exists():
        raise ConfigError(
            f"A pending migration journal exists at {source_journal}. Run "
            f"'steadlith migrate --recover --config {source_path}' before adoption."
        )
    try:
        with source_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"Configuration not found: {source_path}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"Configuration must be valid UTF-8: {source_path}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {source_path}: {exc}") from exc
    config = _config_from_mapping(
        _apply_legacy_defaults(raw),
        base_dir=source_path.parent,
        config_path=source_path,
    )
    rendered = render_config(config, adopted=True)
    try:
        with destination_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
    except FileExistsError as exc:
        raise ConfigError(
            f"Refusing to overwrite existing configuration: {destination_path}"
        ) from exc
    except OSError as exc:
        raise ConfigError(
            f"Could not write adopted configuration {destination_path}: {exc}"
        ) from exc
    return destination_path
