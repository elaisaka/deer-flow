# Personal knowledge extension

Read `docs/KNOWLEDGE_BASE.md` and `docs/RAG.md`. Use public extension contracts,
owner-bound IDs, one SQLite + immutable copies in the configured data volume.
RAG indexes only current ready parsed versions; parse ready is not index ready.
Explicit scopes and trusted run identity are mandatory; recheck ownership,
versions, tombstones and config/generation after embedding. No silent fallback.
Real embeddings require explicit endpoint/model/dimension and allow_send.
No production fake/hash/random vectors; offline doubles belong to tests/evaluator.
Publish all chunks atomically, recover interrupted workers, consume invalidations
idempotently. Background workers are volume-serialized with an OS lock.
Answer model invocation uses the host grant, without tools; excerpts go only in
low-priority user data. Validate citations against this retrieval, recheck source
availability, and escape generated text. Legitimate IDs do not prove support.
Citation locators contain no text snapshots; invalidate on update/delete. Existing
chat ToolMessage excerpts persist separately: document this retention/deletion
policy on the deletion page. Never promise library deletion erases chat history.
Parser workers are bounded and disposable; document text is untrusted data.
Update/delete require browser session/Origin/CSRF routes and user button clicks,
without Windows keys. Bind updates to selected bytes and current revision.
Never expose either operation as model actions/tools.
Keep deletion snapshot-bound, tombstones inaccessible, cleanup retryable, and
version invalidation events available for indexing. Imports use the fixed
Windows endpoint, not host mounts. Tests use synthetic files and temporary stores
in `backend/tests/test_knowledge_{base,extension,local_import}.py` and
`backend/tests/test_personal_rag.py`. Keep real model/embedding acceptance distinct.
