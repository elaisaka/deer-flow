# Personal knowledge extension

Read `docs/KNOWLEDGE_BASE.md`. Use public extension contracts, owner-bound IDs,
SQLite + immutable copies in the configured data volume. No index or model calls.
Parser workers are bounded and disposable; document text is untrusted data.
Update/delete require browser session/Origin/CSRF routes and user button clicks,
without Windows keys. Bind updates to selected bytes and current revision.
Never expose either operation as model actions/tools.
Keep deletion snapshot-bound, tombstones inaccessible, cleanup retryable, and
version invalidation events available for future indexing. Imports use the fixed
Windows endpoint, not host mounts. Tests use synthetic files and temporary stores
in `backend/tests/test_knowledge_{base,extension,local_import}.py`.
