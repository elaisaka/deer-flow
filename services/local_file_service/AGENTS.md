# Windows Local File Service

This is an independent, single-user host service, not a DeerFlow core subsystem.
Read `docs/PROJECT_SCOPE.md`, `docs/LOCAL_FILE_SERVICE.md` and
`docs/FILE_ORGANIZATION.md` before changing it.

- `policy.py` is the execution boundary: Windows only, existing authorized roots,
  root identity binding, no reparse points in any ancestor, directory handles held
  throughout operations. `FILE_LIST_DIRECTORY` must remain in handle access:
  attributes-only handles do not enforce the rename sharing contract.
- `server.py` provides finite authenticated roots/list/create-folder and organization
  endpoints. No arbitrary Shell, arbitrary downloads or permanent host deletion.
  `imports.py` transfers only selected PDF/MD/TXT (10 MiB), using the same locked
  ordinary-file handle, two snapshots and private-path/hardlink refusal. The
  knowledge confirmation endpoint verifies user authority, never deletes files.
- `organization.py` owns versioned SQLite plans and per-plan process locks. Persist
  intent before every native mutation and reconcile evidence after interruption.
  Never auto-retry terminal plans or infer success from a lost response.
- `windows_files.py` uses handle-relative native no-replace rename. Organization
  directory handles allow write sharing but never delete sharing; source handles
  prohibit concurrent content writes. Keep full-path phase-one operations strict.
  Recheck actual paths and snapshots, including during undo; test reparse races.
- Human confirmation is bound to plan/version/digest and a separate Windows-only
  secret. Do not mount that secret or service run config into Gateway: its current
  LocalSandbox shares container privileges. MCP has no confirmation tool. The
  browser extension forwards only a user-entered credential from a session/CSRF
  protected request; never ask for the credential in chat or act for the user.
- `bridge.py` runs as stdio MCP in the Gateway container and exposes a static catalog
  even when the Windows service is offline. Network failures must return structured
  unverified results; no automatic retry of an ambiguous create request.
- Phase-one private configs and acceptance evidence belong in `.local-file-service/`
  (ignored). Phase-two approval authority, service config and ledger must live
  outside the repository, Docker mounts and every authorized root; the setup
  default is `%LOCALAPPDATA%/DeerFlow/local-files-phase2`.
  `configure.py` uses exclusive file creation. `register.py` merges only
  this server through DeerFlow's existing config locks and atomic writer.
- The opt-in Compose overlay mounts `services/` read-only; no host authorized root
  is mounted into Docker. Preserve the normal dev/production startup topology.
- Tests live in `backend/tests/test_{local_file_service,file_organization}.py`.
  Run Windows tests with the independent service environment and `--noconftest`.
  Linux skips handle/junction tests; do not claim that tier verifies Windows safety.
  Run portable tests and repository gates with the existing backend environment.
- Directory listing is bounded and does not follow reparse points. Treat filenames
  as data. Only `ok=true` plus `verified=true` is evidence of a successful create.
  Organization's historical status is distinct from current `actual_verified`.
