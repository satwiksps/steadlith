from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import pytest

from steadlith.content import CorpusManifest, DocumentManifest, chunk_content_hash
from steadlith.errors import BackendError
from steadlith.index.adapters import DocumentState, IndexRecord, SQLiteIndex
from steadlith.models import ChunkRecord


def _record(instance: str, position: int, text: str, vector: tuple[float, ...]) -> IndexRecord:
    return IndexRecord(
        instance_id=instance,
        document_id="guide.md",
        position=position,
        chunk_hash=f"hash-{instance}",
        text=text,
        start_offset=position * 10,
        end_offset=position * 10 + len(text),
        token_count=3,
        metadata={"source": "guide.md"},
        vector=vector,
    )


def _document(count: int) -> DocumentState:
    return DocumentState(
        document_id="guide.md",
        root_hash=f"root-{count}",
        chunk_count=count,
        hard_cuts=0,
        metadata={"path": "guide.md"},
    )


def test_snapshot_delete_is_immediately_unreachable_then_compactable(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    first = _record("one", 0, "removed text", (1.0, 0.0))
    second = _record("two", 1, "kept text", (0.0, 1.0))
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=(first, second),
            documents=(_document(2),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=2,
            expected_generation=0,
        )
        assert index.query((1.0, 0.0), limit=1)[0].text == "removed text"
        with pytest.raises(ValueError, match="index requires 2"):
            index.query((1.0,), limit=1)
        with pytest.raises(ValueError, match="non-empty and finite"):
            index.query((float("nan"), 0.0), limit=1)
        with pytest.raises(BackendError, match="embedding model changed"):
            index.query(
                (1.0, 0.0),
                expected_model_id="hash:other",
                expected_params_hash="params",
            )

        kept = _record("two", 0, "kept text", (0.0, 1.0))
        _, tombstoned = index.apply_snapshot(
            records=(kept,),
            documents=(_document(1),),
            manifest_payload={"root_hash": "corpus-2"},
            corpus_root="corpus-2",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=2,
            expected_generation=1,
        )
        assert tombstoned == 1
        assert [match.text for match in index.query((1.0, 0.0), limit=10)] == ["kept text"]
        assert index.status().tombstoned_chunks == 1
        assert index.compact(dry_run=True) == 1
        assert index.status().tombstoned_chunks == 1
        with pytest.raises(ValueError, match="ISO-8601"):
            index.compact(before="zzz")
        assert index.compact() == 1
        assert index.status().tombstoned_chunks == 0


def test_compaction_cutoff_removes_rows_at_or_before_the_boundary(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    records = (
        _record("before", 0, "before cutoff", (1.0, 0.0)),
        _record("at", 1, "at cutoff", (0.0, 1.0)),
        _record("after", 2, "after cutoff", (1.0, 1.0)),
    )
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=records,
            documents=(_document(3),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=2,
            expected_generation=0,
        )
        index.apply_snapshot(
            records=(),
            documents=(),
            manifest_payload={"root_hash": "corpus-2"},
            corpus_root="corpus-2",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=2,
            expected_generation=1,
        )

    cutoff = "2025-01-01T00:00:00.000000+00:00"
    with sqlite3.connect(path) as connection:
        connection.executemany(
            "UPDATE chunks SET valid_to = ? WHERE instance_id = ?",
            (
                ("2024-12-31T23:59:59.999999+00:00", "before"),
                (cutoff, "at"),
                ("2025-01-01T00:00:00.000001+00:00", "after"),
            ),
        )
        connection.commit()

    with SQLiteIndex(path) as index:
        assert index.compact(before=cutoff, dry_run=True) == 2
        assert index.status().tombstoned_chunks == 3
        assert index.compact(before=cutoff) == 2
        assert index.status().tombstoned_chunks == 1

    with sqlite3.connect(path) as connection:
        remaining = connection.execute("SELECT instance_id FROM chunks").fetchall()
    assert remaining == [("after",)]


def _publish_verified_snapshot(index: SQLiteIndex) -> None:
    chunk_hash = chunk_content_hash(
        "hello",
        chunker_id="test-chunker",
        chunker_params_hash="test-params",
        normalizer_version="test-normalizer",
    )
    record = IndexRecord(
        instance_id="one",
        document_id="guide.md",
        position=0,
        chunk_hash=chunk_hash,
        text="hello",
        start_offset=0,
        end_offset=5,
        token_count=3,
        metadata={"source": "guide.md"},
        vector=(1.0,),
    )
    document = DocumentManifest(
        document_id="guide.md",
        chunks=(ChunkRecord(chunk_hash, 0, 5, 3, {"source": "guide.md"}),),
        metadata={"path": "guide.md"},
        chunker_id="test-chunker",
        chunker_params_hash="test-params",
        normalizer_version="test-normalizer",
    )
    manifest = CorpusManifest({"guide.md": document})
    index.apply_snapshot(
        records=(record,),
        documents=(
            DocumentState(
                document_id="guide.md",
                root_hash=document.root_hash,
                chunk_count=1,
                hard_cuts=0,
                metadata={"path": "guide.md"},
            ),
        ),
        manifest_payload=manifest.to_dict(),
        corpus_root=manifest.root_hash,
        model_id="hash:test",
        params_hash="params",
        vector_dimensions=1,
        expected_generation=0,
    )


def test_index_verify_detects_consistent_snapshot(tmp_path: Path) -> None:
    with SQLiteIndex(tmp_path / "index.sqlite3") as index:
        _publish_verified_snapshot(index)
        assert index.verify() == (True, ())


@pytest.mark.parametrize("metadata", ["[]", "null", '"text"', "1"])
def test_verify_reports_non_object_metadata(tmp_path: Path, metadata: str) -> None:
    path = tmp_path / "index.sqlite3"
    with SQLiteIndex(path) as index:
        _publish_verified_snapshot(index)
        with sqlite3.connect(path) as connection:
            connection.execute("UPDATE chunks SET metadata_json = ?", (metadata,))
        valid, problems = index.verify()
        assert not valid
        assert any("metadata is invalid" in problem for problem in problems)


def test_interrupted_snapshot_rolls_back_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import steadlith.index.adapters.sqlite as adapter

    path = tmp_path / "index.sqlite3"
    with SQLiteIndex(path) as index:
        _publish_verified_snapshot(index)
        initial = index.status()
        initial_records = index.active_records()

        def interrupt(_vector: object) -> bytes:
            raise KeyboardInterrupt

        with monkeypatch.context() as patch:
            patch.setattr(adapter, "_encode_vector", interrupt)
            with pytest.raises(KeyboardInterrupt):
                index.apply_snapshot(
                    records=(_record("replacement", 0, "replacement", (1.0,)),),
                    documents=(_document(1),),
                    manifest_payload={},
                    corpus_root="replacement",
                    model_id="hash:test",
                    params_hash="params",
                    vector_dimensions=1,
                    expected_generation=1,
                )
        assert index.status() == initial
        assert index.active_records() == initial_records
        assert index.verify() == (True, ())
        index.apply_snapshot(
            records=(),
            documents=(),
            manifest_payload=CorpusManifest({}).to_dict(),
            corpus_root=CorpusManifest({}).root_hash,
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=1,
        )
        assert index.status().generation == 2
        assert index.verify() == (True, ())


@pytest.mark.parametrize("operation", ["status", "verify"])
def test_index_reads_one_committed_generation_during_concurrent_publish(
    tmp_path: Path, operation: str
) -> None:
    path = tmp_path / "index.sqlite3"
    with SQLiteIndex(path) as writer:
        _publish_verified_snapshot(writer)
        initial = writer.status()
        with SQLiteIndex(path, readonly=True) as reader:
            connection = reader._connection
            assert connection is not None
            published = False

            def publish_after_metadata(statement: str) -> None:
                nonlocal published
                if published or "FROM documents" not in statement:
                    return
                published = True
                empty = CorpusManifest({})
                writer.apply_snapshot(
                    records=(),
                    documents=(),
                    manifest_payload=empty.to_dict(),
                    corpus_root=empty.root_hash,
                    model_id="hash:test",
                    params_hash="params",
                    vector_dimensions=1,
                    expected_generation=1,
                )

            connection.set_trace_callback(publish_after_metadata)
            if operation == "status":
                assert reader.status() == initial
            else:
                assert reader.verify() == (True, ())
            assert published
            assert reader.status().generation == 2
            assert reader.status().active_chunks == 0


def test_query_rejects_corrupt_candidate_vectors(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    record = _record("one", 0, "hello", (1.0, 0.0))
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=(record,),
            documents=(_document(1),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=2,
            expected_generation=0,
        )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE chunks SET dimensions = 1, vector = ?",
            (sqlite3.Binary(struct.pack("<f", 1.0)),),
        )
        connection.commit()
    with SQLiteIndex(path, readonly=True) as index:
        with pytest.raises(BackendError, match="invalid candidate vector"):
            index.query((1.0, 0.0))

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE chunks SET dimensions = 2, vector = ?",
            (sqlite3.Binary(struct.pack("<2f", float("nan"), 0.0)),),
        )
        connection.commit()
    with SQLiteIndex(path, readonly=True) as index:
        with pytest.raises(BackendError, match="invalid candidate vector"):
            index.query((1.0, 0.0))


def test_interrupted_query_releases_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import steadlith.index.adapters.sqlite as adapter

    path = tmp_path / "index.sqlite3"
    with SQLiteIndex(path) as writer:
        _publish_verified_snapshot(writer)
        with SQLiteIndex(path, readonly=True) as reader:

            def interrupt(*_args: object) -> None:
                raise KeyboardInterrupt

            with monkeypatch.context() as patch:
                patch.setattr(adapter, "_query_match", interrupt)
                with pytest.raises(KeyboardInterrupt):
                    reader.query((1.0,))
            empty = CorpusManifest({})
            writer.apply_snapshot(
                records=(),
                documents=(),
                manifest_payload=empty.to_dict(),
                corpus_root=empty.root_hash,
                model_id="hash:test",
                params_hash="params",
                vector_dimensions=1,
                expected_generation=1,
            )
            assert reader.query((1.0,)) == []
            assert reader.status().generation == 2


def test_status_rejects_corrupt_scalar_counters(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    record = _record("one", 0, "hello", (1.0,))
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=(record,),
            documents=(_document(1),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=0,
        )

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE documents SET hard_cuts = 'broken'")
        connection.commit()
    with SQLiteIndex(path, readonly=True) as index:
        with pytest.raises(BackendError, match="counters are invalid"):
            index.status()


def test_verify_reports_durable_field_and_vector_tampering(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    chunk_hash = chunk_content_hash(
        "hello",
        chunker_id="test-chunker",
        chunker_params_hash="test-params",
        normalizer_version="test-normalizer",
    )
    document = DocumentManifest(
        document_id="guide.md",
        chunks=(ChunkRecord(chunk_hash, 0, 5, 1, {"source": "guide.md"}),),
        metadata={"path": "guide.md"},
        chunker_id="test-chunker",
        chunker_params_hash="test-params",
        normalizer_version="test-normalizer",
    )
    manifest = CorpusManifest({"guide.md": document})
    record = IndexRecord(
        "one", "guide.md", 0, chunk_hash, "hello", 0, 5, 1, {"source": "guide.md"}, (1.0,)
    )
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=(record,),
            documents=(DocumentState("guide.md", document.root_hash, 1, 0, document.metadata),),
            manifest_payload=manifest.to_dict(),
            corpus_root=manifest.root_hash,
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=0,
        )

    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE documents SET root_hash = 'tampered'")
        connection.execute(
            "UPDATE chunks SET text = 'altered', start_offset = 7, model_id = 'other', "
            "vector = x'00000000'"
        )
        connection.commit()

    with SQLiteIndex(path, readonly=True) as index:
        valid, problems = index.verify()
    assert not valid
    joined = "\n".join(problems)
    assert "root hash differs" in joined
    assert "text does not match chunk hash" in joined
    assert "start offset differs" in joined
    assert "embedding identity differs" in joined
    assert "record digest differs" in joined


def test_verify_reports_malformed_scalar_data_without_crashing(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    record = _record("one", 0, "hello", (1.0,))
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=(record,),
            documents=(_document(1),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=0,
        )
    empty_manifest = CorpusManifest({})
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE chunks SET position = 'broken'")
        connection.execute(
            "UPDATE index_meta SET value = ? WHERE key = 'manifest_json'",
            (empty_manifest.to_json(),),
        )
        connection.execute(
            "UPDATE index_meta SET value = ? WHERE key = 'corpus_root'",
            (empty_manifest.root_hash,),
        )

    with SQLiteIndex(path, readonly=True) as index:
        valid, problems = index.verify()
    assert not valid
    assert any("invalid scalar data" in problem for problem in problems)


def test_reused_occurrence_id_rejects_incompatible_identity(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    first = _record("one", 0, "first", (1.0,))
    incompatible = _record("one", 0, "different", (1.0,))
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=(first,),
            documents=(_document(1),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=0,
        )
        with pytest.raises(BackendError, match="reused with incompatible"):
            index.apply_snapshot(
                records=(incompatible,),
                documents=(_document(1),),
                manifest_payload={"root_hash": "corpus-2"},
                corpus_root="corpus-2",
                model_id="hash:test",
                params_hash="params",
                vector_dimensions=1,
                expected_generation=1,
            )
        assert index.status().generation == 1
        assert index.active_records()[0].text == "first"


def test_tombstoned_occurrence_requires_a_new_lifecycle_identifier(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    first = _record("one", 0, "first", (1.0,))
    with SQLiteIndex(path) as index:
        index.apply_snapshot(
            records=(first,),
            documents=(_document(1),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=0,
        )
        index.apply_snapshot(
            records=(),
            documents=(),
            manifest_payload={"root_hash": "empty"},
            corpus_root="empty",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=1,
        )
        with pytest.raises(BackendError, match="cannot be reactivated"):
            index.apply_snapshot(
                records=(first,),
                documents=(_document(1),),
                manifest_payload={"root_hash": "corpus-1"},
                corpus_root="corpus-1",
                model_id="hash:test",
                params_hash="params",
                vector_dimensions=1,
                expected_generation=2,
            )

        replacement = _record("one-v2", 0, "first", (1.0,))
        index.apply_snapshot(
            records=(replacement,),
            documents=(_document(1),),
            manifest_payload={"root_hash": "corpus-1"},
            corpus_root="corpus-1",
            model_id="hash:test",
            params_hash="params",
            vector_dimensions=1,
            expected_generation=2,
        )
        assert index.status().active_chunks == 1
        assert index.status().tombstoned_chunks == 1


def test_snapshot_rejects_stale_generation_inside_transaction(tmp_path: Path) -> None:
    record = _record("one", 0, "hello", (1.0,))
    arguments = {
        "records": (record,),
        "documents": (_document(1),),
        "manifest_payload": {"root_hash": "same-root"},
        "corpus_root": "same-root",
        "model_id": "hash:test",
        "params_hash": "params",
        "vector_dimensions": 1,
        "expected_generation": 0,
    }
    with SQLiteIndex(tmp_path / "index.sqlite3") as index:
        index.apply_snapshot(**arguments)
        assert index.status().generation == 1

        with pytest.raises(BackendError, match="changed after this plan"):
            index.apply_snapshot(**arguments)

        assert index.status().generation == 1


def test_invalid_index_schema_version_is_a_typed_backend_error(tmp_path: Path) -> None:
    path = tmp_path / "index.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE index_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.execute("INSERT INTO index_meta VALUES ('schema_version', 'broken')")
    with pytest.raises(BackendError, match="Invalid index schema version"):
        SQLiteIndex(path)
    with pytest.raises(BackendError, match="Invalid index schema version"):
        SQLiteIndex(path, readonly=True)


def test_snapshot_rejects_non_finite_vectors_before_writing(tmp_path: Path) -> None:
    record = _record("one", 0, "hello", (float("nan"),))
    with SQLiteIndex(tmp_path / "index.sqlite3") as index:
        with pytest.raises(BackendError, match="only finite"):
            index.apply_snapshot(
                records=(record,),
                documents=(_document(1),),
                manifest_payload={"root_hash": "root"},
                corpus_root="root",
                model_id="hash:test",
                params_hash="params",
                vector_dimensions=1,
                expected_generation=0,
            )
        assert index.status().generation == 0
