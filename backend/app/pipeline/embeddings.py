"""Local sentence embeddings and an in-memory vector index.

`all-MiniLM-L6-v2` runs on CPU, needs no API key, and encodes a few thousand
short claims in under a second. Since facts are one-line claims rather than
paragraphs, a small model is the right call: the extra nuance of a large model
buys nothing here, and the linker only uses similarity as a *recall* device -
the actual decision is made by rules plus an LLM judge downstream.

The index is a plain float32 matrix with cosine similarity via a dot product on
normalised vectors. At our scale (thousands of facts) an exact scan is
sub-millisecond, which is faster than the round trip to any vector database
would be, with none of the operational surface.
"""

from __future__ import annotations

import logging
import threading
from typing import Iterable

import numpy as np

from ..config import settings
from ..db import query

log = logging.getLogger("fkl.embed")

_model = None
_model_lock = threading.Lock()


def get_model():
    """Load the encoder once, lazily (first call downloads ~90MB)."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                log.info("loading embedding model %s", settings.embedding_model)
                _model = SentenceTransformer(settings.embedding_model, device="cpu")
    return _model


def embed_texts(texts: list[str]) -> np.ndarray:
    """Encode to L2-normalised float32 so cosine similarity is a dot product."""
    if not texts:
        return np.zeros((0, 384), dtype=np.float32)
    model = get_model()
    vectors = model.encode(
        texts,
        batch_size=32,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return np.asarray(vectors, dtype=np.float32)


def to_blob(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes, dim: int) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32, count=dim)


class VectorIndex:
    """Facts already in the store, held as one matrix for scanning.

    Built once per ingest run and extended in memory as new facts are added, so
    a document with 400 facts costs one database read rather than 400.
    """

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.doc_ids: list[str] = []
        self.matrix: np.ndarray = np.zeros((0, 0), dtype=np.float32)

    @classmethod
    def load(cls) -> "VectorIndex":
        index = cls()
        rows = query(
            """SELECT e.fact_id, e.dim, e.vec, f.doc_id
               FROM fact_embeddings e
               JOIN facts f ON f.id = e.fact_id"""
        )
        if not rows:
            return index
        dim = rows[0]["dim"]
        matrix = np.empty((len(rows), dim), dtype=np.float32)
        for i, row in enumerate(rows):
            matrix[i] = from_blob(row["vec"], row["dim"])
            index.ids.append(row["fact_id"])
            index.doc_ids.append(row["doc_id"])
        index.matrix = matrix
        return index

    def __len__(self) -> int:
        return len(self.ids)

    def add(self, fact_ids: Iterable[str], doc_ids: Iterable[str], vectors: np.ndarray) -> None:
        fact_ids, doc_ids = list(fact_ids), list(doc_ids)
        if not fact_ids:
            return
        if self.matrix.size == 0:
            self.matrix = np.asarray(vectors, dtype=np.float32)
        else:
            self.matrix = np.vstack([self.matrix, np.asarray(vectors, dtype=np.float32)])
        self.ids.extend(fact_ids)
        self.doc_ids.extend(doc_ids)

    def search(
        self,
        vector: np.ndarray,
        *,
        top_k: int,
        threshold: float,
        exclude_fact_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """Return (fact_id, cosine) above `threshold`, best first."""
        if self.matrix.size == 0:
            return []
        scores = self.matrix @ np.asarray(vector, dtype=np.float32)
        # Take a generous slice before filtering so exclusions cannot starve the
        # result set.
        want = min(len(self.ids), max(top_k * 4, top_k + 8))
        candidate_idx = np.argpartition(-scores, want - 1)[:want] if len(self.ids) > want else np.arange(len(self.ids))
        ranked = sorted(candidate_idx, key=lambda i: -scores[i])

        out: list[tuple[str, float]] = []
        for i in ranked:
            fact_id = self.ids[i]
            if exclude_fact_ids and fact_id in exclude_fact_ids:
                continue
            score = float(scores[i])
            if score < threshold:
                break
            out.append((fact_id, score))
            if len(out) >= top_k:
                break
        return out
