# Caeliae Read — phase 2 physical-directory migration evidence

Date: 2026-09-17. Baseline: `7fdc3d898ba80f0c93ab5fb4d0fdca5ce41cfddc`
on `feature/coread-v0.1-annotations`, with a clean worktree.

## Result

The physical directory moved from:

`<legacy-repo-root>`

to:

`<repo-root>`

The old path no longer exists and the new path contains `.git`, `data`, `docs`,
`src`, `tests`, and `web`. Git history and the branch were not rewritten.

## Consistency backup

Backup root:

`<backup-root>\20260917-caeliae-read`

- `coread.sqlite3` was created with SQLite's backup API.
- Source and backup both reported `integrity_check=ok`, an empty
  `foreign_key_check`, and `journal_mode=wal`.
- Tables and counts matched: `papers=3`, `annotations=8`,
  `annotation_events=9`, `client_events=254`, `reading_sessions=67`,
  `reading_states=55`, `selections=10`, `schema_migrations=2`.
- All three files in `data/papers` were copied and SHA-256 matched the source;
  total size was 4,822,462 bytes.
- `coread-core.bundle` was verified as a complete Git history containing the
  feature branch, master, archive tag, and HEAD.
- `codex-config.toml` and `coread-core.yaml` are non-sensitive rollback copies.
  The DPAPI file stayed in place and was not read or copied.
- `migration-manifest.json`, `python-freeze.txt`, and the database/paper
  verification JSON files record versions, hashes, and comparison results.

## Environment and dependencies

- `.venv.pre-caeliae-read` preserves the previous environment for rollback.
- A new `.venv` was created with Python 3.12.14 and installed from the recorded
  package freeze; editable metadata points to `caeliae-read`.
- `web/node_modules` was removed and reinstalled from the unchanged
  `pnpm-lock.yaml`; no junction points target `coread-core`.
- Existing `coread-api`, `coread-mcp`, and `python -m coread_core.mcp.server`
  remain thin delegates. New and legacy launchers returned the same six-tool
  `tools/list` result in isolated databases.

## External path updates

- `%USERPROFILE%\.codex\config.toml` now points to the new Python executable,
  `-m caeliae_read.mcp.server`, and the new `cwd`. `enabled=false` remains in
  place while local MCP is intentionally paused.
- The existing `coread-core` Tunnel profile keeps its name, Tunnel ID, admin
  port, and DPAPI path. Only its launcher now points to
  `start-caeliae-read-mcp.cmd`.
- The same Tunnel profile was restarted. Local checks returned API
  `/api/health` 200 (`service=caeliae-read`, `version=0.1.0`) and Tunnel
  `/readyz` 200 (`ready`). This is local health evidence only, not Work-side
  `tools/list` or six-tool acceptance.
- The `icloud-calendar` Tunnel process and configuration were not touched.

## Validation

- New-directory backend: **34 passed**, two existing dependency warnings.
- Frontend: `pnpm build` passed.
- New stdio initialize/tools-list/schema audit passed with all six tools.
- New and legacy launchers both returned the six expected tool names.
- Full isolated browser regression passed, including detached cross-page cards,
  lower-page non-zero scroll, 150%/200% zoom, remember revoke, idempotency,
  stdio-to-web polling, compact header/toolbar, and responsive layouts.
- Post-move database integrity/counts and paper SHA-256 values matched the
  consistency backup.

## Rollback materials and limits

The old environment and all backup materials remain; nothing was deleted. A
rollback must stop the confirmed new-directory API/Tunnel instances first and
must not overwrite newer database state with the backup without a separate
explicit decision. Formal Work-side six-tool provenance and ChatGPT host-sidebar
visual acceptance remain pending.
