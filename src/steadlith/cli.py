"""The ``steadlith`` command-line interface."""

from __future__ import annotations

import argparse
import dataclasses
import math
import sys
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

from steadlith import __version__
from steadlith._legacy_wire import LEGACY_CONFIG_FILENAME
from steadlith.config import (
    SteadlithConfig,
    adopt_legacy_config,
    load_config,
    write_default_config,
)
from steadlith.errors import BackendError, ConfigError, ExitCode, SteadlithError, VerificationError
from steadlith.index.plan import OperationKind
from steadlith.index.service import (
    _protected_state,
    apply_prepared,
    compact_index,
    index_status,
    prepare_index,
    query_index,
    verify_index,
)
from steadlith.migrate import (
    apply_migration,
    prepare_migration,
    prepare_rollback,
    recover_pending_migration,
)
from steadlith.report import (
    json_output,
    render_cache_stats,
    render_mapping_rows,
    render_plan,
    render_query_matches,
    render_status,
)
from steadlith.store import Cache

Handler = Callable[[argparse.Namespace, Console], int]

_BENCHMARK_CORPORA = (
    "field-guide",
    "quartz-docs",
    "harbor-lease",
    "ledger-repository",
    "island-wiki",
)
_BENCHMARK_EDITS = (
    "insert-sentence",
    "insert-paragraph",
    "insert-section",
    "delete-sentence",
    "delete-paragraph",
    "replace-same-length",
    "reorder-sections",
    "append",
    "global-replace",
)


def _make_output_encoding_safe(stream: Any) -> None:
    """Prevent user-controlled Unicode from crashing legacy text streams."""

    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(errors="backslashreplace")
        except (AttributeError, OSError, ValueError):
            pass


def _config(args: argparse.Namespace) -> SteadlithConfig:
    return load_config(args.config)


def _status_dict(status: Any) -> dict[str, Any]:
    payload = dataclasses.asdict(status)
    payload["hard_cut_rate"] = status.hard_cut_rate
    return payload


def _init(args: argparse.Namespace, console: Console) -> int:
    path = write_default_config(args.config, force=args.force)
    if args.json:
        json_output(
            {
                "created": str(path),
                "backend": "sqlite",
                "next": "review the sources table, then run steadlith plan",
            },
            console=console,
        )
    else:
        console.print(
            Text.assemble("Created ", (str(path), "bold"), " with the offline SQLite backend.")
        )
        console.print(
            "Review the [bold]sources[/bold] table, then run [bold]steadlith plan[/bold]."
        )
    return int(ExitCode.SUCCESS)


def _adopt(args: argparse.Namespace, console: Console) -> int:
    path = adopt_legacy_config(args.from_config, args.config)
    if args.json:
        json_output(
            {
                "created": str(path),
                "source": str(Path(args.from_config).expanduser().resolve()),
                "configured_state_paths_preserved": True,
                "state_moved": False,
                "embeddings_created": False,
            },
            console=console,
        )
    else:
        console.print(Text.assemble(("Adopted", "green"), " configuration at ", str(path)))
        console.print("Configured state paths and model identity were preserved. No data moved.")
    return int(ExitCode.SUCCESS)


def _plan(args: argparse.Namespace, console: Console) -> int:
    prepared = prepare_index(_config(args), args.paths)
    if args.json:
        json_output(prepared.plan.as_dict(), console=console)
    else:
        render_plan(prepared.plan, console=console)
    return int(ExitCode.SUCCESS)


def _index(args: argparse.Namespace, console: Console) -> int:
    config = _config(args)
    if config.embedding.provider != "hash" and not args.allow_network:
        raise ConfigError(
            "This provider may use the network or download code/models; rerun index with "
            f"--allow-network after reviewing {args.config} and the source scope"
        )
    prepared = prepare_index(config, args.paths)
    deletes = prepared.plan.counts[OperationKind.DELETE]
    if deletes and not args.allow_delete:
        raise ConfigError(
            f"Plan would tombstone {deletes:,} active chunks; review 'steadlith plan' and "
            "rerun with --allow-delete"
        )
    if prepared.plan.old_chunks and not prepared.plan.new_chunks and not args.allow_empty:
        raise ConfigError(
            "Plan would make the corpus empty; rerun with both --allow-delete and --allow-empty"
        )
    if not args.json:
        render_plan(prepared.plan, console=console)
    result = apply_prepared(prepared)
    if args.json:
        json_output(result.as_dict(), console=console)
    else:
        console.print(
            f"[green]Applied[/green] {result.active_chunks:,} active chunks; "
            f"embedded {result.embedded_chunks:,}, reused {result.cache_hits:,} from cache, "
            f"tombstoned {result.tombstoned_chunks:,}."
        )
    return int(ExitCode.SUCCESS)


def _status(args: argparse.Namespace, console: Console) -> int:
    status = index_status(_config(args))
    if args.json:
        json_output(_status_dict(status), console=console)
    else:
        render_status(status, console=console)
    return int(ExitCode.SUCCESS)


def _query(args: argparse.Namespace, console: Console) -> int:
    config = _config(args)
    if config.embedding.provider != "hash" and not args.allow_network:
        raise ConfigError(
            "This provider may use the network or download code/models; rerun query with "
            f"--allow-network after reviewing {args.config}"
        )
    matches = list(query_index(config, args.text, limit=args.limit))
    if args.json:
        json_output(
            {
                "query": args.text,
                "count": len(matches),
                "matches": [dataclasses.asdict(match) for match in matches],
            },
            console=console,
        )
    else:
        render_query_matches(matches, console=console)
    return int(ExitCode.SUCCESS)


def _verify(args: argparse.Namespace, console: Console) -> int:
    valid, problems = verify_index(_config(args))
    if args.json:
        json_output({"valid": valid, "problems": list(problems)}, console=console)
    elif valid:
        console.print("[green]Verified[/green]: active index and manifest agree.")
    else:
        console.print("[red]Verification failed[/red]")
        for problem in problems:
            console.print(Text(f"  - {problem}"))
    return int(ExitCode.SUCCESS if valid else ExitCode.VERIFICATION_MISMATCH)


def _compact(args: argparse.Namespace, console: Console) -> int:
    before = args.before
    if before is not None:
        try:
            parsed = datetime.fromisoformat(before.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ConfigError("--before must be a valid ISO-8601 timestamp") from exc
        if parsed.tzinfo is None:
            raise ConfigError("--before must include a timezone offset")
    removed = compact_index(_config(args), before=before, dry_run=args.dry_run)
    if args.json:
        json_output({"eligible" if args.dry_run else "removed": removed}, console=console)
    elif args.dry_run:
        console.print(f"{removed:,} tombstoned vector rows are eligible for compaction.")
    else:
        console.print(f"Purged {removed:,} tombstoned vector rows.")
    return int(ExitCode.SUCCESS)


def _migrate(args: argparse.Namespace, console: Console) -> int:
    override_values = {
        "chunker.strategy": args.chunker,
        "chunker.window_words": args.window_words,
        "chunker.min_tokens": args.min_tokens,
        "chunker.max_tokens": args.max_tokens,
        "chunker.snap_window_words": args.snap_window_words,
        "chunker.primary_mask_bits": args.primary_mask_bits,
        "chunker.backup_mask_bits": args.backup_mask_bits,
        "embedding.provider": args.embedding_provider,
        "embedding.model": args.embedding_model,
        "embedding.dimensions": args.embedding_dimensions,
    }
    overrides = {key: value for key, value in override_values.items() if value is not None}
    if args.recover:
        if (
            args.apply
            or args.dry_run
            or args.rollback
            or args.paths
            or overrides
            or args.allow_network
            or args.allow_delete
            or args.allow_empty
        ):
            raise ConfigError("--recover cannot be combined with migration planning options")
        recovery = recover_pending_migration(args.config)
        if args.json:
            json_output(recovery.as_dict(), console=console)
        else:
            if recovery.outcome == "committed":
                console.print(
                    Text.assemble(
                        ("Recovered", "green"),
                        f" migration {recovery.migration_id}; receipt: {recovery.receipt}",
                    )
                )
            elif recovery.outcome == "aborted":
                console.print(
                    f"Cleared uncommitted migration {recovery.migration_id}; index unchanged."
                )
            else:
                console.print("No pending migration journal exists.")
        return int(ExitCode.SUCCESS)
    if args.apply and args.paths:
        raise ConfigError(
            "Migration apply uses the persisted [sources] scope; positional PATHs are preview-only"
        )

    if args.rollback:
        if overrides:
            raise ConfigError("--rollback cannot be combined with forward migration settings")
        migration = prepare_rollback(args.config, args.paths)
    else:
        if not overrides:
            raise ConfigError("migrate requires a config override, --rollback, or --recover")
        migration = prepare_migration(args.config, args.paths, overrides=overrides)

    prepared = migration.prepared_index
    config = migration.desired_config
    if args.apply and config.embedding.provider != "hash" and not args.allow_network:
        raise ConfigError(
            "This migration may use the network or download code/models; rerun with "
            "--allow-network after reviewing the plan"
        )
    deletes = prepared.plan.counts[OperationKind.DELETE]
    embedding_identity_changed = (
        migration.old_model_id != prepared.model_id
        or migration.old_params_hash != prepared.params_hash
    )
    tombstones = prepared.plan.old_chunks if embedding_identity_changed else deletes
    if args.apply and tombstones and not args.allow_delete:
        raise ConfigError(
            f"Migration would tombstone {tombstones:,} active chunks; rerun with --allow-delete"
        )
    if (
        args.apply
        and prepared.plan.old_chunks
        and not prepared.plan.new_chunks
        and not args.allow_empty
    ):
        raise ConfigError(
            "Migration would make the corpus empty; rerun with both --allow-delete and "
            "--allow-empty"
        )
    if args.apply:
        result = apply_migration(migration)
        if args.json:
            json_output(
                {"migration": migration.as_dict(), "result": result.as_dict()},
                console=console,
            )
        else:
            console.print(
                Text.assemble(
                    ("Applied migration", "green"),
                    f" {result.migration_id}; generation {prepared.expected_generation + 1}, "
                    f"receipt: {result.receipt}",
                )
            )
        return int(ExitCode.SUCCESS)
    if args.json:
        json_output(migration.as_dict(), console=console)
    else:
        render_plan(prepared.plan, console=console)
        console.print("Configuration changes:")
        for name, change in migration.changes.items():
            console.print(Text(f"  {name}: {change['from']!r} -> {change['to']!r}"))
        console.print(
            "[yellow]Preview only. Evaluate retrieval, then rerun with --apply and any "
            "required safety confirmations.[/yellow]"
        )
    return int(ExitCode.SUCCESS)


def _cache_stats(args: argparse.Namespace, console: Console) -> int:
    config = _config(args)
    with Cache(config.resolve(config.store.cache), readonly=True) as cache:
        stats = cache.stats()
    if args.json:
        json_output(stats.as_dict(), console=console)
    else:
        render_cache_stats(stats, console=console)
    return int(ExitCode.SUCCESS)


def _cache_prune(args: argparse.Namespace, console: Console) -> int:
    if args.max_age_days is None and args.max_entries is None:
        raise ConfigError("cache prune requires --max-age-days or --max-entries")
    if args.max_age_days is not None and args.max_age_days < 0:
        raise ConfigError("--max-age-days cannot be negative")
    if args.max_entries is not None and args.max_entries < 0:
        raise ConfigError("--max-entries cannot be negative")
    config = _config(args)
    with Cache(config.resolve(config.store.cache)) as cache:
        removed = cache.prune(
            max_age_days=args.max_age_days,
            max_entries=args.max_entries,
        )
    if args.json:
        json_output({"removed": removed}, console=console)
    else:
        console.print(f"Pruned {removed:,} cache entries.")
    return int(ExitCode.SUCCESS)


def _cache_export(args: argparse.Namespace, console: Console) -> int:
    config = _config(args)
    destination = args.destination.expanduser().resolve()
    if destination == config.resolve(config.index.database):
        raise ConfigError("Cache export destination cannot overwrite the configured index")
    cache_path = config.resolve(config.store.cache)
    if destination in {Path(f"{cache_path}{suffix}").resolve() for suffix in ("", "-wal", "-shm")}:
        raise BackendError("Cache export destination cannot be the live cache or its sidecars")
    protected_files, protected_directories = _protected_state(config)
    if destination in {path.resolve() for path in protected_files} or any(
        destination == directory.resolve() or directory.resolve() in destination.parents
        for directory in protected_directories
    ):
        raise ConfigError(
            f"Cache export destination cannot overwrite managed project state: {destination}. "
            "Choose a separate export file, even when using --force."
        )
    with Cache(config.resolve(config.store.cache), readonly=True) as cache:
        count = cache.export_jsonl(destination, force=args.force)
    if args.json:
        json_output({"exported": count, "destination": str(destination)}, console=console)
    else:
        console.print(Text(f"Exported {count:,} entries to {destination}."))
    return int(ExitCode.SUCCESS)


def _cache_import(args: argparse.Namespace, console: Console) -> int:
    if not args.trust_source:
        raise ConfigError(
            "Cache imports can affect retrieval results; inspect the file and pass --trust-source"
        )
    config = _config(args)
    with Cache(config.resolve(config.store.cache)) as cache:
        count = cache.import_jsonl(args.source, trusted=True)
    if args.json:
        json_output({"imported": count, "source": str(args.source)}, console=console)
    else:
        console.print(Text(f"Imported {count:,} cache entries from {args.source}."))
    return int(ExitCode.SUCCESS)


def _churn(args: argparse.Namespace, console: Console) -> int:
    from steadlith.measure.churn import EmbeddingPrice, benchmark_churn, default_strategies
    from steadlith.measure.corpora import EditOperation, get_corpus

    if not math.isfinite(args.price) or args.price < 0:
        raise ConfigError("--price must be a finite, non-negative number")
    strategies = default_strategies()
    if args.strategy:
        unknown = sorted(set(args.strategy) - set(strategies))
        if unknown:
            raise ConfigError(
                f"Unknown benchmark strategy: {', '.join(unknown)}. "
                f"Choose from: {', '.join(sorted(strategies))}"
            )
        strategies = {name: strategies[name] for name in args.strategy}
    corpora = [get_corpus(name) for name in args.corpus] if args.corpus else None
    operations = [EditOperation(name) for name in args.edit] if args.edit else None
    kwargs: dict[str, Any] = {
        "strategies": strategies,
        "price": EmbeddingPrice(args.price, args.price_label),
    }
    if corpora is not None:
        kwargs["corpora"] = corpora
    if operations is not None:
        kwargs["operations"] = operations
    result = benchmark_churn(**kwargs)
    if args.json:
        json_output(result.as_dict(), console=console)
    else:
        render_mapping_rows(list(result.summary_rows()), title="Churn benchmark", console=console)
        console.print(f"[yellow]{result.fixture_notice}[/yellow]")
    return int(ExitCode.SUCCESS)


def _retrieval(args: argparse.Namespace, console: Console) -> int:
    from steadlith.measure.churn import default_strategies
    from steadlith.measure.corpora import get_corpus
    from steadlith.measure.retrieval import ScoringMethod, benchmark_retrieval

    if args.k <= 0:
        raise ConfigError("-k must be a positive integer")
    strategies = default_strategies()
    if args.strategy:
        unknown = sorted(set(args.strategy) - set(strategies))
        if unknown:
            raise ConfigError(
                f"Unknown benchmark strategy: {', '.join(unknown)}. "
                f"Choose from: {', '.join(sorted(strategies))}"
            )
        strategies = {name: strategies[name] for name in args.strategy}
    corpora = [get_corpus(name) for name in args.corpus] if args.corpus else None
    kwargs: dict[str, Any] = {
        "strategies": strategies,
        "scoring_method": ScoringMethod(args.scoring),
        "k": args.k,
    }
    if corpora is not None:
        kwargs["corpora"] = corpora
    result = benchmark_retrieval(**kwargs)
    if args.json:
        json_output(result.as_dict(), console=console)
    else:
        render_mapping_rows(
            list(result.summary_rows()), title="Retrieval benchmark", console=console
        )
        console.print(f"[yellow]{result.fixture_notice}[/yellow]")
    return int(ExitCode.SUCCESS)


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "-c",
        "--config",
        default="steadlith.toml",
        help="configuration file (default: steadlith.toml)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="steadlith",
        description="Reuse unchanged RAG chunks with cache-aware transactional indexing.",
    )
    parser.add_argument("--version", action="version", version=f"steadlith {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = _common_parser()

    init = subparsers.add_parser("init", parents=[common], help="write a starter config")
    init.add_argument("--force", action="store_true", help="replace an existing config")
    init.set_defaults(handler=_init)

    adopt = subparsers.add_parser(
        "adopt", parents=[common], help="adopt an earlier v0.2 config without rebuilding state"
    )
    adopt.add_argument(
        "--from-config",
        default=LEGACY_CONFIG_FILENAME,
        help=f"earlier config to adopt (default: {LEGACY_CONFIG_FILENAME})",
    )
    adopt.set_defaults(handler=_adopt)

    plan = subparsers.add_parser("plan", parents=[common], help="preview an index delta")
    plan.add_argument("paths", nargs="*", help="complete desired corpus (defaults to config globs)")
    plan.set_defaults(handler=_plan)

    index = subparsers.add_parser("index", parents=[common], help="embed and apply an index delta")
    index.add_argument(
        "paths", nargs="*", help="complete desired corpus (defaults to config globs)"
    )
    index.add_argument(
        "--allow-network",
        action="store_true",
        help="allow a configured provider to use the network or download models",
    )
    index.add_argument(
        "--allow-delete",
        action="store_true",
        help="confirm tombstoning chunks omitted from the complete desired corpus",
    )
    index.add_argument(
        "--allow-empty",
        action="store_true",
        help="confirm making a previously non-empty corpus empty (also needs --allow-delete)",
    )
    index.set_defaults(handler=_index)

    status = subparsers.add_parser(
        "status",
        parents=[common],
        help="show committed index state",
        description=(
            "Show committed index state without reading configured sources. "
            "Run 'steadlith plan' to compare current sources or embedding configuration."
        ),
    )
    status.set_defaults(handler=_status)

    query = subparsers.add_parser("query", parents=[common], help="retrieve active chunks")
    query.add_argument("text", help="query text to embed and search")
    query.add_argument(
        "-k", "--limit", type=int, default=5, help="maximum results to return (default: 5)"
    )
    query.add_argument(
        "--allow-network",
        action="store_true",
        help="allow a configured provider to use the network or download models",
    )
    query.set_defaults(handler=_query)

    verify = subparsers.add_parser("verify", parents=[common], help="verify index and manifest")
    verify.set_defaults(handler=_verify)

    compact = subparsers.add_parser("compact", parents=[common], help="purge tombstoned vectors")
    compact.add_argument("--before", help="purge tombstones at or before an ISO-8601 timestamp")
    compact.add_argument("--dry-run", action="store_true", help="report eligible rows only")
    compact.set_defaults(handler=_compact)

    migrate = subparsers.add_parser(
        "migrate", parents=[common], help="plan, apply, recover, or roll back a migration"
    )
    migrate.add_argument(
        "paths", nargs="*", help="preview corpus override (not accepted with --apply)"
    )
    migrate.add_argument(
        "--chunker",
        choices=("cdc-rabin", "cdc-rabin+snap", "fixed", "recursive", "semantic"),
    )
    migrate.add_argument("--window-words", type=int)
    migrate.add_argument("--min-tokens", type=int)
    migrate.add_argument("--max-tokens", type=int)
    migrate.add_argument("--snap-window-words", type=int)
    migrate.add_argument("--primary-mask-bits", type=int)
    migrate.add_argument("--backup-mask-bits", type=int)
    migrate.add_argument(
        "--embedding-provider", choices=("hash", "openai", "sentence-transformers")
    )
    migrate.add_argument("--embedding-model")
    migrate.add_argument("--embedding-dimensions", type=int)
    migration_action = migrate.add_mutually_exclusive_group()
    migration_action.add_argument(
        "--apply", action="store_true", help="apply the reviewed plan and persist config"
    )
    migration_action.add_argument(
        "--dry-run", action="store_true", help="explicit preview (also the default)"
    )
    migrate.add_argument(
        "--rollback", action="store_true", help="target the immediately current migration"
    )
    migrate.add_argument(
        "--recover", action="store_true", help="finish or clear an interrupted migration"
    )
    migrate.add_argument(
        "--allow-network",
        action="store_true",
        help="allow a provider to use the network or download models",
    )
    migrate.add_argument("--allow-delete", action="store_true", help="confirm migration tombstones")
    migrate.add_argument(
        "--allow-empty", action="store_true", help="confirm an empty target corpus"
    )
    migrate.set_defaults(handler=_migrate)

    measure = subparsers.add_parser("measure", help="run reproducible regression benchmarks")
    measure_sub = measure.add_subparsers(dest="measure_command", required=True)
    churn = measure_sub.add_parser("churn", help="measure re-embedding volume")
    churn.add_argument("--json", action="store_true")
    churn.add_argument("--strategy", action="append")
    churn.add_argument("--corpus", action="append", choices=_BENCHMARK_CORPORA)
    churn.add_argument("--edit", action="append", choices=_BENCHMARK_EDITS)
    churn.add_argument("--price", type=float, default=0.02, help="USD per million tokens")
    churn.add_argument("--price-label", default="illustrative caller-supplied CLI value")
    churn.set_defaults(handler=_churn)
    retrieval = measure_sub.add_parser("retrieval", help="measure recall and nDCG")
    retrieval.add_argument("--json", action="store_true")
    retrieval.add_argument("--strategy", action="append")
    retrieval.add_argument("--corpus", action="append", choices=_BENCHMARK_CORPORA)
    retrieval.add_argument("--scoring", choices=("lexical", "hash-embedding"), default="lexical")
    retrieval.add_argument("-k", type=int, default=5)
    retrieval.set_defaults(handler=_retrieval)

    cache = subparsers.add_parser("cache", help="manage the embedding cache")
    cache_sub = cache.add_subparsers(dest="cache_command", required=True)
    cache_stats = cache_sub.add_parser("stats", parents=[common])
    cache_stats.set_defaults(handler=_cache_stats)
    cache_prune = cache_sub.add_parser("prune", parents=[common])
    cache_prune.add_argument("--max-age-days", type=int)
    cache_prune.add_argument("--max-entries", type=int)
    cache_prune.set_defaults(handler=_cache_prune)
    cache_export = cache_sub.add_parser("export", parents=[common])
    cache_export.add_argument("destination", type=Path)
    cache_export.add_argument("--force", action="store_true", help="replace an existing export")
    cache_export.set_defaults(handler=_cache_export)
    cache_import = cache_sub.add_parser("import", parents=[common])
    cache_import.add_argument("source", type=Path)
    cache_import.add_argument(
        "--trust-source",
        action="store_true",
        help="confirm that the unsigned cache artifact is trusted",
    )
    cache_import.set_defaults(handler=_cache_import)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _make_output_encoding_safe(sys.stdout)
    _make_output_encoding_safe(sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()
    handler: Handler = args.handler
    try:
        return handler(args, console)
    except VerificationError as exc:
        if getattr(args, "json", False):
            json_output(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "exit_code": int(exc.exit_code),
                },
                console=console,
            )
        else:
            Console(stderr=True).print(
                Text.assemble(("Verification failed: ", "bold red"), str(exc))
            )
        return int(exc.exit_code)
    except SteadlithError as exc:
        if getattr(args, "json", False):
            json_output(
                {
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "exit_code": int(exc.exit_code),
                },
                console=console,
            )
        else:
            Console(stderr=True).print(Text.assemble(("Error: ", "bold red"), str(exc)))
        return int(exc.exit_code)
    except KeyboardInterrupt:
        Console(stderr=True).print("[yellow]Interrupted.[/yellow]")
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
