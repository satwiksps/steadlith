"""Exercise an installed distribution with only its runtime dependencies.

Run this file with the clean wheel environment's Python interpreter. Every CLI
command runs outside the checkout, without PYTHONPATH or editable-install imports.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Any


def smoke(project: Path) -> None:
    executable = "steadlith.exe" if os.name == "nt" else "steadlith"
    cli = Path(sysconfig.get_path("scripts")) / executable
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}

    def execute(arguments: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            arguments,
            cwd=project,
            env=environment,
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=60,
            check=False,
        )

    def run(*arguments: str, expected: int = 0) -> dict[str, Any]:
        result = execute([str(cli), *arguments, "--json"])
        assert result.returncode == expected, (arguments, result.stdout, result.stderr)
        assert not result.stderr, (arguments, result.stderr)
        payload: dict[str, Any] = json.loads(result.stdout)
        print(f"{' '.join(arguments)}: exit {result.returncode}")
        return payload

    module = execute([sys.executable, "-I", "-m", "steadlith", "--version"])
    console = execute([str(cli), "--version"])
    assert module.returncode == console.returncode == 0, (module, console)
    assert module.stdout == console.stdout and module.stdout.startswith("steadlith ")
    assert not module.stderr and not console.stderr

    run("init")
    docs = project / "docs"
    docs.mkdir()
    source = docs / "example.md"
    source.write_text(
        "# Notes\n\nSteadlith reuses embeddings for unchanged RAG chunks.\n", encoding="utf-8"
    )
    plan = run("plan")
    assert plan["counts"] == {"add": 1, "keep": 0, "move": 0, "delete": 0}
    assert plan["cost"]["chunks_to_embed"] == 1
    assert plan["cost"]["tokens_to_embed"] == 9
    assert not (project / ".steadlith").exists(), "preview created state"
    indexed = run("index")
    assert indexed["active_chunks"] == indexed["embedded_chunks"] == 1
    status = run("status")
    assert status["documents"] == status["active_chunks"] == 1
    matches = run("query", "unchanged RAG chunks")["matches"]
    assert len(matches) == 1 and matches[0]["document_id"] == "docs/example.md"
    assert math.isclose(matches[0]["score"], 0.5773502691896258, abs_tol=1e-6)
    assert matches[0]["text"] == "# Notes\n\nSteadlith reuses embeddings for unchanged RAG chunks."
    assert run("verify")["valid"] is True
    repeated = run("index")
    assert repeated["embedded_chunks"] == 0
    assert repeated["plan"]["counts"]["keep"] == 1

    source.write_text("Fresh evidence about SQLite transaction recovery.\n", encoding="utf-8")
    assert run("index", expected=4)["error_type"] == "ConfigError"
    assert run("status")["corpus_root"] == status["corpus_root"]
    edited = run("index", "--allow-delete")
    assert edited["active_chunks"] == edited["embedded_chunks"] == 1
    assert "Fresh evidence" in run("query", "transaction recovery")["matches"][0]["text"]

    config = project / "steadlith.toml"
    original_config = config.read_bytes()
    run("migrate", "--embedding-dimensions", "128")
    assert config.read_bytes() == original_config
    run("migrate", "--embedding-dimensions", "128", "--apply", "--allow-delete")
    assert config.read_bytes() != original_config
    assert run("verify")["valid"] is True
    run("migrate", "--rollback", "--apply", "--allow-delete")
    assert config.read_bytes() == original_config
    assert run("verify")["valid"] is True
    assert run("migrate", "--recover")["outcome"] == "none"

    exported = run("cache", "export", "embeddings.jsonl")["exported"]
    assert exported >= 2
    assert run("cache", "export", "steadlith.toml", "--force", expected=4)["error_type"] == (
        "ConfigError"
    )
    assert config.read_bytes() == original_config
    assert run("cache", "prune", "--max-entries", "0")["removed"] == exported
    assert run("cache", "import", "embeddings.jsonl", "--trust-source")["imported"] == exported
    assert run("index")["embedded_chunks"] == 0

    source.unlink()
    assert run("plan")["counts"]["delete"] == 1
    run("index", "--allow-delete", expected=4)
    assert run("index", "--allow-delete", "--allow-empty")["active_chunks"] == 0
    empty = run("query", "transaction recovery", expected=3)
    assert empty["error_type"] == "BackendError"
    assert empty["error"] == "Index contains no active chunks"
    assert run("verify")["valid"] is True
    eligible = run("compact", "--dry-run")["eligible"]
    assert eligible > 0
    assert run("compact")["removed"] == eligible
    assert run("verify")["valid"] is True

    retrieval = run("measure", "retrieval", "--strategy", "cdc-rabin")["results"]
    assert len(retrieval) == 1
    assert retrieval[0]["question_count"] == 8
    assert retrieval[0]["mean_recall_at_k"] == 1.0


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="steadlith-wheel-") as directory:
        smoke(Path(directory))
    print("Installed distribution workflows passed.")
