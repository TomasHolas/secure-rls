"""Tenant-filtered retrieval over the employees notes (ADR 0010).

The same four RLS layers as the SQL path, applied to vectors. Every indexed note inherits the
`tenant_id` of its source row - chunk-level ACL inheritance - and a search binds the caller's
tenant into the KNN query, so sqlite-vec's partition-key pre-filter restricts the candidate set
BEFORE any vector comparison. Foreign vectors never participate in scoring, which is what makes
the isolation structural rather than a filter applied to results.

- layer 1: `tenant_id` is a parameter of `search_notes_scoped`, supplied by the caller from the
  verified JWT. Nothing here reads it from the query text, the model, or the environment.
- layer 2: there is no generated SQL to validate - `db.py` owns the one fixed KNN shape.
- layer 3: the bound tenant plus the vec0 partition key, inside `db.search_vectors`.
- layer 4: `_verify_tenant` re-checks every returned chunk's own `tenant_id` and raises
  `db.SecurityViolation` on a mismatch, so a store built or mutated wrongly cannot leak.

No match is an empty list, identical whether nothing was close or the only close note belongs to
another tenant; the tool layer words the neutral message. Storage and queries go through `db.py`,
the only module that opens a connection; this module owns embedding and orchestration.

The endpoint address stays out of here: `OllamaEmbed` takes `base_url` from the caller, so the
app wiring is the single place that reads `OLLAMA_BASE_URL` (ADR 0005).
"""

from pathlib import Path
from typing import Protocol

import httpx

import db
from runtime import runtime

_EMBED_PATH = "/api/embed"


class EmbedClient(Protocol):
    """What this module needs of an embedder: one vector per text, in the same order."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return the embedding of each text, positionally aligned with the input."""
        ...


class OllamaEmbed:
    """The production embedder: the configured embedding model over an Ollama endpoint."""

    def __init__(self, base_url: str, model: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model or runtime().agent.embed_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed texts in batches, one POST per batch so a full index is not one huge request."""
        config = runtime().rag
        vectors: list[list[float]] = []
        with httpx.Client(base_url=self._base_url, timeout=config.embed_timeout_s) as client:
            for start in range(0, len(texts), config.embed_batch_size):
                batch = texts[start : start + config.embed_batch_size]
                response = client.post(_EMBED_PATH, json={"model": self._model, "input": batch})
                response.raise_for_status()
                vectors.extend(response.json()["embeddings"])
        return vectors


def index_notes(db_path: Path, embedder: EmbedClient) -> int:
    """Embed every tenant's notes and rebuild the partitioned store; returns the number indexed."""
    notes = db.notes_for_indexing(db_path)
    vectors = embedder.embed([note.note for note in notes])
    db.init_vector_store(db_path, notes, vectors)
    return len(notes)


def search_notes_scoped(
    db_path: Path, embedder: EmbedClient, query: str, tenant_id: str, k: int | None = None
) -> list[dict[str, object]]:
    """The notes closest to query inside tenant_id's partition; an empty list when none match."""
    (vector,) = embedder.embed([query])
    matches = db.search_vectors(
        db_path, vector, tenant_id, runtime().rag.top_k if k is None else k
    )
    _verify_tenant(matches, tenant_id)
    return [
        {"user_id": match.user_id, "name": match.name, "note": match.note,
         "distance": match.distance}
        for match in matches
    ]


def _verify_tenant(matches: list[db.VectorMatch], tenant_id: str) -> None:
    """Layer 4 for retrieval: refuse a result set carrying any chunk from another tenant."""
    for match in matches:
        if match.tenant_id != tenant_id:
            raise db.SecurityViolation(
                f"a retrieved note carries tenant {match.tenant_id!r}, not {tenant_id!r}",
                kind="rag_egress_mismatch",
            )
