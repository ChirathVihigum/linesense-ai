"""Text embedders for the retrieval pipeline (backend-contracts.md, task-17-brief.md).

``FastEmbedEmbedder`` wraps ``fastembed.TextEmbedding`` (the real model,
``BAAI/bge-small-en-v1.5`` by default, 384 dimensions, cached under
``.local/models`` on first use — a one-off network download). Every chunk
stores the ``embedding_model`` it was embedded with, so a later switch of
model never silently mixes incompatible vectors (see
``app.retrieval.search``).

``HashingEmbedder`` is a deterministic, dependency-free stand-in for tests:
feature hashing of lower-cased word unigrams and bigrams into 384
dimensions, L2-normalized. It must never be selected in production.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol, runtime_checkable

from app.settings import Settings

DIMENSION = 384
HASHING_MODEL_NAME = "hashing-384-test"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@runtime_checkable
class Embedder(Protocol):
    model_name: str
    dimension: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class FastEmbedEmbedder:
    """The real embedder, backed by ``fastembed.TextEmbedding``."""

    dimension = DIMENSION

    def __init__(self, model_name: str) -> None:
        # Imported lazily: fastembed pulls in onnxruntime/tokenizers, and a
        # first construction downloads the model (~100MB) to `.local/models`
        # if it is not already cached there. Never imported by test code,
        # which uses `HashingEmbedder` instead.
        from fastembed import TextEmbedding  # noqa: PLC0415

        self.model_name = model_name
        self._model = TextEmbedding(model_name=model_name, cache_dir=".local/models")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        # fastembed already L2-normalizes bge-small output.
        return [vector.tolist() for vector in self._model.embed(list(texts))]


class HashingEmbedder:
    """Deterministic feature-hashed embedder for tests only.

    Lower-cased word unigrams and bigrams are hashed (SHA-256, so the
    mapping is stable across processes and Python versions unlike the
    salted builtin ``hash()``) into one of 384 buckets with a signed
    contribution (the standard hashing-trick sign bit, to keep unrelated
    tokens from biasing the vector in one direction), then the whole vector
    is L2-normalized.
    """

    model_name = HASHING_MODEL_NAME
    dimension = DIMENSION

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        tokens = _TOKEN_RE.findall(text.lower())
        vector = [0.0] * self.dimension
        for token in _ngrams(tokens):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(component * component for component in vector))
        if norm == 0.0:
            return vector
        return [component / norm for component in vector]


def _ngrams(tokens: list[str]) -> list[str]:
    unigrams = list(tokens)
    bigrams = [f"{a} {b}" for a, b in zip(tokens, tokens[1:], strict=False)]
    return unigrams + bigrams


class UnknownEmbedderError(ValueError):
    pass


@lru_cache(maxsize=4)
def _cached_fastembed(model_name: str) -> FastEmbedEmbedder:
    """Loading the ONNX model is slow; reuse one instance per process/model."""
    return FastEmbedEmbedder(model_name)


def build_embedder(settings: Settings) -> Embedder:
    """``LS_EMBEDDER`` selects the embedder; ``hashing`` is refused in production."""
    if settings.embedder == "hashing":
        if settings.environment == "production":
            raise UnknownEmbedderError("LS_EMBEDDER=hashing is not allowed in production")
        return HashingEmbedder()
    if settings.embedder == "fastembed":
        return _cached_fastembed(settings.embedding_model)
    raise UnknownEmbedderError(f"unknown LS_EMBEDDER {settings.embedder!r}")
