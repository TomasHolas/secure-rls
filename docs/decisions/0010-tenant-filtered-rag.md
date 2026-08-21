# ADR 0010 — Tenant-filtered RAG over notes (sqlite-vec partition keys)

Status: accepted

## Context

The assignment's main goal covers leakage "via LLM queries or retrievals" and
asks to consider RAG where applicable. The `notes` column (free-text performance
reviews) is a natural semantic-search corpus ("who shows leadership
potential?"). A retrieval path we secure is a stronger answer than one we avoid
— multi-tenant isolation inside RAG is a documented risk class (OWASP LLM08,
Vector and Embedding Weaknesses: context leakage between users in shared vector
databases).

## Decision

A `search_notes(query)` tool implementing the industry security-trimming
pattern, with the same four layers as the SQL path:

- **Chunk-level ACL inheritance**: every embedded note stores the `tenant_id`
  of its source row — the Azure AI Search security-filter pattern (identity
  field on each indexed document) and the AWS Bedrock multi-tenant metadata
  pattern.
- **Embeddings**: `nomic-embed-text` via the same configured Ollama endpoint
  (`POST /api/embed`), computed once at load time. No new service.
- **Storage**: a `vec0` virtual table (sqlite-vec) with
  `tenant_id text partition key` — the index is internally sharded per tenant,
  and the KNN query's `AND tenant_id = ?` restricts the search BEFORE any
  vectors are compared. True pre-filtering: foreign vectors never participate
  in similarity scoring, avoiding the recall loss and existence leakage that
  post-filtering is documented to cause (Pinecone, pgvector).
- **Layer mapping**: L1 tenant bound from the verified JWT by closure (AWS:
  the metadata filter is "a critical security boundary," constructed
  server-side from verified identity — never model-supplied); L2 trivial (the
  KNN query is a fixed parameterized shape, no generated SQL); L3 the
  partition-key pre-filter with a bound parameter; L4 egress check on every
  returned chunk's `tenant_id`.
- **Zero results are neutral**: "no matching notes found" — identical wording
  whether nothing matches or matching data exists in another tenant. Derived
  from RFC 9110's 404-instead-of-403 allowance and OWASP's generic-error rule
  (no authoritative RAG-specific source exists — labeled judgment); the
  pre-filter makes the property structural regardless.
- **Version pinning**: sqlite-vec is pre-v1 ("expect breaking changes") — the
  exact version is pinned and the tenant-isolation invariant is covered by
  tests that re-run on any upgrade.

## Implementation notes (added after issue #18 landed)

- Empirical authorizer map for a vec0 KNN read: SQLITE_SELECT, SQLITE_FUNCTION
  on `match`, and SQLITE_READ on the virtual table plus its `_rowids`,
  `_chunks`, and `_auxiliary` shadow tables; the remaining shadow tables go
  through the blob API and never consult the authorizer.
- **sqlite-vec 0.1.9 segfaults (exit 139) if the authorizer denies the
  `_rowids` shadow read** - an unchecked statement-prepare in the pre-v1
  extension. Consequence: the vector index lives in its own `vectors.db`,
  and the connection executing model-generated SQL caps attached databases
  at zero - so the crash is unreachable from any generated query, by
  construction rather than by filter.
- The tenant partition predicate is provably load-bearing: dropping it
  returns rows from every tenant (tested), and the isolation test is rigged
  so it cannot pass vacuously - the only semantically-close note belongs to
  the foreign tenant.

## Consequences

- The demo gains a third secured data path with zero new security code paths —
  the same four layers, applied to vectors.
- The eval suite gains cross-tenant retrieval attacks ("find notes about
  employees at beta" must return zero foreign chunks).
- One new pinned dependency (sqlite-vec) and one more Ollama model to pull on
  the endpoint machine (checked in the M2 smoke gate, ADR 0005).
- Cleanly descopeable if the time budget tightens: nothing else depends on it.

## Alternatives

- **Schema card only, no retrieval** — legitimate and cheap, but leaves the
  assignment's "retrievals" clause uncovered and forfeits the strongest
  discussion topic.
- **Dedicated vector DB (Qdrant/Weaviate/Pinecone)** — their multitenancy
  features are the reference designs, but a second engine violates the
  single-SQLite story at this scale; noted as the production evolution.
- **Post-filtering retrieved chunks** — rejected: documented recall loss
  (Pinecone; pgvector's iterative-scan fix exists because of it) and foreign
  vectors would participate in ranking.

## References

- OWASP LLM08:2025 Vector and Embedding Weaknesses —
  https://genai.owasp.org/llmrisk/llm082025-vector-and-embedding-weaknesses/
- Azure AI Search, security trimming / security filter pattern —
  https://learn.microsoft.com/en-us/azure/search/search-security-trimming-for-azure-search
- AWS, multi-tenant RAG with Bedrock KB metadata filtering —
  https://aws.amazon.com/blogs/machine-learning/multi-tenancy-in-rag-applications-in-a-single-amazon-bedrock-knowledge-base-with-metadata-filtering
- AWS, multi-tenant RAG with JWT (OpenSearch) —
  https://aws.amazon.com/blogs/machine-learning/multi-tenant-rag-implementation-with-amazon-bedrock-and-amazon-opensearch-service-for-saas-using-jwt/
- Pinecone, "The Missing WHERE Clause in Vector Search" (pre- vs
  post-filtering) — https://www.pinecone.io/learn/vector-search-filtering/
- Qdrant multitenancy (partition by payload, filterable HNSW) —
  https://qdrant.tech/documentation/manage-data/multitenancy/
- pgvector README (post-filter recall problem, iterative scans) —
  https://github.com/pgvector/pgvector
- sqlite-vec: vec0 metadata + partition keys —
  https://alexgarcia.xyz/sqlite-vec/features/vec0.html,
  https://alexgarcia.xyz/blog/2024/sqlite-vec-metadata-release/index.html,
  https://github.com/asg017/sqlite-vec
- RFC 9110 section 15.5.4 (404 to hide existence) —
  https://www.rfc-editor.org/rfc/rfc9110.html
- OWASP REST Security Cheat Sheet (generic error messages) —
  https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html
- Ollama embedding models — https://ollama.com/blog/embedding-models
