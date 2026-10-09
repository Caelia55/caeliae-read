# Caeliae Read development log (historically Coread Core)

## 2026-09-18 — Caeliae Read v0.1 final acceptance and archival

- Formal Work currently exposes six tools through the existing `coread-core`
  Secure Tunnel: `list_papers`, `get_current_reading_context`,
  `get_current_selection`, `get_checkpoint`, `list_annotations`, and
  `create_assistant_annotation`.
- End-to-end source association is recorded as formal Work → `coread-core`
  Secure Tunnel → the new-directory `caeliae_read.mcp.server` stdio. The
  evidence is the refreshed Work tool prefix, disabled local Codex MCP, the
  single unchanged Tunnel connection, the unique write, and its local
  persistence match. The tunnel-client log level did not provide a
  per-request dispatcher line; no such log evidence is claimed.
- Synthetic Work write `1eb0b852-a5d0-4151-9e69-b37d447b4e5e` returned
  `replayed=false` initially and the same ID with `replayed=true` on an
  identical replay; exactly one matching persistent record remained. The
  separate polling write `e70af97b-2dbf-433c-a04d-22834f29e922` appeared in
  the unrefreshed reader within the polling interval.
- Manual visual acceptance uses reachable states: desktop keeps the complete
  toolbar; compact mode reaches a 30px slim toolbar after more than 96px of
  real `.page-stage` scrolling; an open annotation card keeps header and
  toolbar expanded by design. Content viewport width and compact state are the
  evidence fields, not the sidebar toggle alone.
- Final regression and archival metadata are recorded in
  `docs/final-acceptance.md`. Historical four-tool Work acceptance and the
  earlier local-stdio write remain unchanged historical records.

## 2026-09-17 — phase 2 physical-directory migration

- After the old stdio sessions were disabled and the migration gate passed, the
  repository moved from `coread-core` to `caeliae-read` without rewriting Git
  history. The old venv remains as `.venv.pre-caeliae-read` for rollback.
- SQLite backup/API integrity, WAL mode, foreign-key checks, schema migration
  count, key table counts, paper SHA-256 values, and a complete Git bundle were
  verified before the move and rechecked after it. Evidence is in
  `docs/migration-phase2.md` and the external migration-backups directory.
- A fresh Python 3.12.14 venv and fresh pnpm dependencies were built at the new
  path. Codex MCP remains temporarily disabled while its command/cwd point to
  the new implementation; the existing `coread-core` Tunnel profile now
  launches `start-caeliae-read-mcp.cmd` without changing identity, port, or
  DPAPI placement.
- Local `/api/health` and Tunnel `/readyz` passed, and all isolated backend,
  frontend, stdio, browser, cross-page, zoom, remember, idempotency, and
  responsive regressions passed. This does not claim Work-side six-tool
  discovery or final ChatGPT sidebar visual acceptance.

## 2026-09-17 — card remains reachable after leaving its source page

- Fixed the annotation-card state machine so an open card is global to the
  active annotation rather than the currently rendered PDF page. Page, zoom,
  toolbar and responsive actions no longer count as an outside-click close.
- When navigation leaves the source page, the card remains visible in a safe
  detached state, keeps its active annotation id, shows the source page, and
  exposes `回到原文`. Returning uses an explicit render-ready/pending-focus
  path, then focuses the current page's normalized quad and scrolls the real
  `.page-stage` container; it does not use a guessed render timeout.
- Isolated real-paper browser acceptance passed at 125% and 200%: same-anchor
  user/assistant switching, page-1 detachment and return, page-2 lower-page
  return with measured `scrollTop` 447/1074, card retention, and assistant
  delete restriction. The prior automatic-cross-page blocked result remains a
  historical pre-fix finding and was not rewritten.
- No physical directory migration, formal Tunnel/profile change, process
  restart, iCloud access, or real-user database write was performed.

## 2026-09-17 — Caeliae Read phase 1 software naming migration

- New public module name: Caeliae Read; distribution/frontend/MCP server:
  `caeliae-read`; Python implementation: `caeliae_read`.
- Physical `coread-core` directory, `data/coread.sqlite3`, schema versions,
  annotation semantics, URLs, ports, PWA scope and six tool names are unchanged.
- Legacy CLI/module/batch entries delegate to the new implementation and are
  deletion candidates after phase 2 deployment migration. `COREAD_*` environment
  aliases last one migration version, with stderr-only value-free warnings;
  conflicting DATA_ROOT values fail instead of silently choosing a database.
- Browser state migrates legacy localStorage keys after successful restoration.
- Source research and independent-build conclusion are in `docs/provenance.md`.
- Historical Coread names below remain historical facts, not retroactive branding.
- Acceptance boundaries: historical formal Tunnel/Work four-tool acceptance;
  local stdio six-tool acceptance; formal Tunnel/Work six-tool discovery and
  assistant write provenance are still pending. No formal profile was changed or
  restarted for this migration; no iCloud service was accessed.
- Validation: 34 backend tests, frontend build, and seven isolated browser scripts
  passed. Real-paper geometry, remember revoke, polling/idempotency, compact chrome,
  legacy localStorage restore and same-anchor focus were exercised. Manual page
  navigation followed by source refocus passed; card-triggered automatic cross-page
  navigation was not fully revalidated and is not claimed as passed here.
- Detailed file scope, data-contract evidence and retained-name reasons are in
  `docs/migration-phase1.md`. This is not a v0.1 formal Tunnel archival acceptance.

## 2026-09-13 — v0.1 reader focus, responsive closeout, local stdio create path

- Annotation marks now open a card through one focus path. The card can return to
  the source mark, cross-page focus changes the PDF page before scrolling the
  current mark into view, and identical page/text/quad anchors expose explicit
  switch buttons. Focus survives PDF rerender and zoom because anchors remain
  normalized to the page.
- Narrow layouts use shared shell/control/status variables; at very narrow widths
  the header, compact toolbar, upload control, and status footer reduce together.
- Local stdio reports the six tools: `list_papers`, `get_current_reading_context`,
  `get_current_selection`, `get_checkpoint`, `list_annotations`, and
  `create_assistant_annotation`.
- Source correction (2026-09-17): local stdio, not a proven formal Work Tunnel
  connector, performed the real `create_assistant_annotation`
  call created assistant remember `fcc17fcd-8332-4a8a-8c47-5261b6966bc9` with
  idempotency key `coread-v01-work-remember-20260913`; the returned author and
  `remember=true` were verified by a subsequent annotation listing; replaying
  the exact payload returned `replayed=true` with the same annotation id.
- Validation: 17 backend tests passed; `pnpm build` passed; existing browser
  acceptance passed page-2 selection restore and four responsive viewports.
- Tunnel restart was not forced: two `tunnel-client.exe` processes are present,
  while Windows denied command-line inspection. No process was stopped by name;
  this did not prove the formal Work connector had the six-tool version.

## 2026-09-12 — v0封存 / ChatGPT Work验收

- Independent Secure MCP Tunnel connected to ChatGPT Work successfully.
- Work remotely discovered and called exactly four read-only MCP tools:
  `list_papers`, `get_current_reading_context`, `get_current_selection`, and
  `get_checkpoint`.
- Remote acceptance confirmed paper listing, current page text, a selection, and
  the current revision. Sensitive values and detailed paper content are omitted.
- Runtime Key and DPAPI material remain outside the repository and are not logged.
- Startup order, clean stop, reboot recovery, and port-ownership guidance are in
  `README.md`.
- No paper files, SQLite rows, selections, checkpoints, or reading state were
  modified during this archival update.
- v0 remains limited to the four read-only tools. PDF annotation work is deferred
  to the separately planned v0.1 phase.

## 2026-09-12 — v0.1 annotation read/render slice

- Frontend fetches persisted annotations and overlays normalized page-relative
  marks without modifying the source PDF or text layer.
- Target resolution is page-scoped; unresolved targets are omitted safely.
- Author identity stays semantic; visual defaults live in replaceable CSS theme
  tokens, not annotation records.
- Clicking a mark opens a compact card; refresh reconstructs marks from the API.
- Backend schema and all six MCP tools remain intact.
- Validation: 13 backend tests passed; `pnpm build` passed, with existing bundle
  size and dependency deprecation advisories.
- Remaining: user create/delete UI, stronger annotation tests, live Work refresh,
  full card↔text navigation, and final v0.1 acceptance.
