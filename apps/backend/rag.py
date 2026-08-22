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

Availability (ADR 0010 as amended). The index is built at startup by `ensure_index`, which is
idempotent: a store already holding THIS corpus is left alone, so a restart costs no embeddings.
Which corpus it holds is decided by a fingerprint written with the vectors, so a regenerated
dataset re-embeds instead of being searched through the old embeddings. An index that was never
built is an operator condition, not a model error - `search_notes_scoped` raises
`RetrievalUnavailable` for it, which the tool layer turns into a plain statement that retrieval is
offline instead of an error the model would retry three times.
"""

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import httpx

import db
from runtime import runtime

_EMBED_PATH = "/api/embed"
_UNAVAILABLE = "the note index has not been built on this server"


class RetrievalUnavailable(Exception):
    """Raised when no note index exists, so retrieval cannot serve any tenant at all."""


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


def corpus_fingerprint(notes: Sequence[db.NoteRow]) -> str:
    """A digest of the corpus about to be indexed, stable across runs and machines.

    Every field the store serves is hashed, not the note text alone: a renamed employee would
    otherwise keep being returned under the name the index was built with. The unit separators
    keep the digest unambiguous, so no pair of corpora can hash to the same concatenation.
    """
    digest = hashlib.sha256()
    for note in notes:
        digest.update(
            f"{note.tenant_id}\x1f{note.user_id}\x1f{note.name}\x1f{note.note}\x1e".encode()
        )
    return digest.hexdigest()


def index_notes(db_path: Path, embedder: EmbedClient) -> int:
    """Embed every tenant's notes and rebuild the partitioned store; returns the number indexed."""
    return _index(db_path, embedder, db.notes_for_indexing(db_path))


def ensure_index(db_path: Path, embedder: EmbedClient) -> int:
    """Index the notes unless the store already holds this exact corpus; returns how many it holds.

    The stored fingerprint is what makes skipping safe (ADR 0010 as amended): a store built from
    a different corpus - a regenerated dataset, an edited note - is stale, and serving its
    embeddings would answer questions about text that no longer exists. Rows held alone cannot
    tell the two apart, so it re-embeds whenever the digest differs.
    """
    notes = db.notes_for_indexing(db_path)
    held = db.vector_store_rows(db_path)
    if held and db.vector_store_fingerprint(db_path) == corpus_fingerprint(notes):
        return held
    return _index(db_path, embedder, notes)


def _index(db_path: Path, embedder: EmbedClient, notes: Sequence[db.NoteRow]) -> int:
    """Embed the given notes and rebuild the store, stamped with the digest of what was embedded."""
    vectors = embedder.embed([note.note for note in notes])
    db.init_vector_store(db_path, notes, vectors, corpus_fingerprint(notes))
    return len(notes)


def search_notes_scoped(
    db_path: Path, embedder: EmbedClient, query: str, tenant_id: str, k: int | None = None
) -> list[dict[str, object]]:
    """The notes closest to query inside tenant_id's partition; an empty list when none match."""
    (vector,) = embedder.embed([query])
    try:
        matches = db.search_vectors(
            db_path, vector, tenant_id, runtime().rag.top_k if k is None else k
        )
    except FileNotFoundError as missing:
        raise RetrievalUnavailable(_UNAVAILABLE) from missing
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
