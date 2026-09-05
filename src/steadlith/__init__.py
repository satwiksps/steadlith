"""Stable public surface for Steadlith's dependency-light core."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from steadlith.chunk.cdc import CDCChunker
from steadlith.chunk.params import CDCParams
from steadlith.models import Chunk
from steadlith.store import Cache

try:
    __version__ = version("steadlith")
except PackageNotFoundError:  # source checkout without an installed distribution
    __version__ = "1.0.1"

__all__ = ["CDCChunker", "CDCParams", "Cache", "Chunk", "__version__"]
