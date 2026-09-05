from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace

import pytest

from steadlith.embed.providers.hash import HashEmbeddingProvider
from steadlith.embed.providers.openai import OpenAIEmbeddingProvider
from steadlith.embed.providers.sentence_transformers import SentenceTransformersProvider
from steadlith.errors import ProviderError, TransientProviderError


def test_hash_provider_rejects_unbounded_or_ambiguous_dimensions() -> None:
    with pytest.raises(ValueError, match="between 1"):
        HashEmbeddingProvider(dimensions=65_537)
    with pytest.raises(ValueError, match="between 1"):
        HashEmbeddingProvider(dimensions=True)  # type: ignore[arg-type]


def test_sentence_transformer_load_failures_are_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("sentence_transformers")

    class BrokenModel:
        def __init__(self, model: str) -> None:
            raise RuntimeError(f"cannot load {model}")

    module.SentenceTransformer = BrokenModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    with pytest.raises(ProviderError, match="Could not load local embedding model"):
        SentenceTransformersProvider(model="broken", dimensions=8)


def test_openai_client_initialization_failures_are_typed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("openai")

    class ConnectionFailure(Exception):
        pass

    class BrokenClient:
        def __init__(self, **kwargs: object) -> None:
            del kwargs
            raise RuntimeError("client initialization failed")

    module.APIConnectionError = ConnectionFailure  # type: ignore[attr-defined]
    module.APITimeoutError = ConnectionFailure  # type: ignore[attr-defined]
    module.OpenAI = BrokenClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    with pytest.raises(ProviderError, match="Could not initialize"):
        OpenAIEmbeddingProvider(model="test", dimensions=8)


def test_openai_client_disables_sdk_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("openai")
    captured: dict[str, object] = {}

    class SDKError(Exception):
        pass

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    module.APIConnectionError = SDKError  # type: ignore[attr-defined]
    module.APITimeoutError = SDKError  # type: ignore[attr-defined]
    module.OpenAI = Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    OpenAIEmbeddingProvider(model="test", dimensions=8)

    assert captured["max_retries"] == 0


def test_openai_ignores_environment_endpoint_override(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("openai")
    captured: dict[str, object] = {}

    class SDKError(Exception):
        pass

    class Client:
        def __init__(self, *, base_url: str | None = None, **kwargs: object) -> None:
            del kwargs
            captured["endpoint"] = base_url or os.environ.get("OPENAI_BASE_URL")

    module.APIConnectionError = SDKError  # type: ignore[attr-defined]
    module.APITimeoutError = SDKError  # type: ignore[attr-defined]
    module.OpenAI = Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unintended.example/v1")

    OpenAIEmbeddingProvider(model="test", dimensions=8)

    assert captured["endpoint"] == "https://api.openai.com/v1"


def test_openai_success_preserves_response_order_and_request_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("openai")
    captured: dict[str, object] = {}

    class SDKError(Exception):
        pass

    class Embeddings:
        def create(self, *, model: str, input: list[str], dimensions: int) -> SimpleNamespace:
            captured.update(model=model, input=input, dimensions=dimensions)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=1, embedding=[0.0, 1.0]),
                    SimpleNamespace(index=0, embedding=[1.0, 0.0]),
                ]
            )

    class Client:
        def __init__(self, **kwargs: object) -> None:
            captured["client"] = kwargs
            self.embeddings = Embeddings()

    module.APIConnectionError = SDKError  # type: ignore[attr-defined]
    module.APITimeoutError = SDKError  # type: ignore[attr-defined]
    module.OpenAI = Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", module)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    provider = OpenAIEmbeddingProvider(model="test", dimensions=2)

    assert provider.embed(["first", "second"]) == [(1.0, 0.0), (0.0, 1.0)]
    assert captured["model"] == "test"
    assert captured["input"] == ["first", "second"]
    assert captured["dimensions"] == 2


@pytest.mark.parametrize("indices", [(0, 0), (1,), (0, 2)])
def test_openai_rejects_duplicate_missing_or_out_of_range_indices(
    indices: tuple[int, ...],
) -> None:
    response = SimpleNamespace(
        data=[SimpleNamespace(index=index, embedding=[1.0, 0.0]) for index in indices]
    )
    provider = OpenAIEmbeddingProvider.__new__(OpenAIEmbeddingProvider)
    provider.model = "test"
    provider._dimensions = 2
    provider._transient_error_types = ()
    provider._client = SimpleNamespace(
        embeddings=SimpleNamespace(create=lambda **_kwargs: response)
    )

    with pytest.raises(ProviderError, match="duplicate, missing, or out-of-range"):
        provider.embed(["first", "second"])


def test_sentence_transformer_success_preserves_request_and_response_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = ModuleType("sentence_transformers")
    captured: dict[str, object] = {}

    class Model:
        def __init__(self, model: str) -> None:
            captured["model"] = model

        def get_sentence_embedding_dimension(self) -> int:
            return 2

        def encode(
            self,
            texts: list[str],
            *,
            normalize_embeddings: bool,
            show_progress_bar: bool,
        ) -> list[list[float]]:
            captured.update(
                texts=texts,
                normalize_embeddings=normalize_embeddings,
                show_progress_bar=show_progress_bar,
            )
            return [[1.0, 0.0], [0.0, 1.0]]

    module.SentenceTransformer = Model  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    provider = SentenceTransformersProvider(model="local-test", dimensions=2)

    assert provider.embed(["first", "second"]) == [(1.0, 0.0), (0.0, 1.0)]
    assert provider.model_id == "sentence-transformers:local-test"
    assert captured == {
        "model": "local-test",
        "texts": ["first", "second"],
        "normalize_embeddings": True,
        "show_progress_bar": False,
    }


def test_openai_connection_failure_is_retryable() -> None:
    class ConnectionFailure(Exception):
        pass

    class Embeddings:
        def create(self, **kwargs: object) -> None:
            del kwargs
            raise ConnectionFailure("offline")

    provider = OpenAIEmbeddingProvider.__new__(OpenAIEmbeddingProvider)
    provider.model = "test"
    provider._dimensions = 8
    provider._transient_error_types = (ConnectionFailure,)
    provider._client = SimpleNamespace(embeddings=Embeddings())

    with pytest.raises(TransientProviderError, match="offline"):
        provider.embed(["query"])
