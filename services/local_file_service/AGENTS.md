# Windows Local File Service

This is an independent, single-user host service, not a DeerFlow core subsystem.
Read `docs/PROJECT_SCOPE.md` and `docs/LOCAL_FILE_SERVICE.md` before changing it.

- `policy.py` is the execution boundary: Windows only, existing authorized roots,
  root identity binding, no reparse points in any ancestor, directory handles held
  throughout operations. `FILE_LIST_DIRECTORY` must remain in handle access:
  attributes-only handles do not enforce the rename sharing contract.
- `server.py` provides only authenticated roots/list/create-folder HTTP endpoints.
  Never add arbitrary shell execution, file reads, deletion or rename in phase one.
- `bridge.py` runs as stdio MCP in the Gateway container and exposes a static catalog
  even when the Windows service is offline. Network failures must return structured
  unverified results; no automatic retry of an ambiguous create request.
- Private configs, tokens and acceptance evidence belong in `.local-file-service/`
  (ignored). `configure.py` uses exclusive file creation. `register.py` merges only
  this server through DeerFlow's existing config locks and atomic writer.
- The opt-in Compose overlay mounts `services/` read-only; no host authorized root
  is mounted into Docker. Preserve the normal dev/production startup topology.
- Tests live in `backend/tests/test_local_file_service.py`. Run Windows execution
  tests with `python -m pytest --noconftest backend/tests/test_local_file_service.py -q`.
  Linux skips handle/junction tests; do not claim that tier verifies Windows safety.
  Run portable tests and repository gates with the existing backend environment.
- Directory listing is bounded and does not follow reparse points. Treat filenames
  as data. Only `ok=true` plus `verified=true` is evidence of a successful create.
