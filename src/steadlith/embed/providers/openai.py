"""Optional OpenAI embedding provider (installed with ``steadlith[openai]``)."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Sequence
from typing import Any

from steadlith._legacy_wire import V1_EMBEDDING_PARAMS_PREFIX
from steadlith.errors import ProviderError, TransientProviderError


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
    ) -> None:
        try:
            from openai import APIConnectionError, APITimeoutError, OpenAI
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ProviderError(
                "OpenAI support is not installed; run 'pip install steadlith[openai]'"
            ) from exc
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ProviderError(f"Embedding API key environment variable is unset: {api_key_env}")
        self.model = model
        self._dimensions = dimensions
        self._transient_error_types = (APIConnectionError, APITimeoutError)
        endpoint = base_url or "https://api.openai.com/v1"
        try:
            # Passing None lets the SDK substitute OPENAI_BASE_URL, bypassing the
            # configured endpoint restriction and disagreeing with cache identity.
            self._client: Any = OpenAI(api_key=api_key, base_url=endpoint, max_retries=0)
        except Exception as exc:
            raise ProviderError(f"Could not initialize the OpenAI client: {exc}") from exc
        payload = json.dumps(
            {
                "base_url": endpoint,
                "dimensions": dimensions,
                "model": model,
                "provider": "openai",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self._params_hash = hashlib.sha256(V1_EMBEDDING_PARAMS_PREFIX + payload).hexdigest()

    @property
    def model_id(self) -> str:
        return f"openai:{self.model}"

    @property
    def params_hash(self) -> str:
        return self._params_hash

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(
                model=self.model,
                input=list(texts),
                dimensions=self._dimensions,
            )
        except Exception as exc:  # provider SDK exceptions vary by version
            status_code = getattr(exc, "status_code", None)
            transient = (
                isinstance(exc, self._transient_error_types)
                or status_code
                in {
                    408,
                    409,
                    429,
                }
                or (isinstance(status_code, int) and status_code >= 500)
            )
            error_type = TransientProviderError if transient else ProviderError
            raise error_type(f"OpenAI embedding request failed: {exc}") from exc
        try:
            ordered = sorted(response.data, key=lambda item: int(item.index))
            indices = [int(item.index) for item in ordered]
            if indices != list(range(len(texts))):
                raise ProviderError(
                    "OpenAI returned duplicate, missing, or out-of-range embedding indices"
                )
            return [tuple(float(value) for value in item.embedding) for item in ordered]
        except ProviderError:
            raise
        except (AttributeError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError(f"OpenAI returned a malformed embedding response: {exc}") from exc
