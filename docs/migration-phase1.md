# Caeliae Read — phase 1 software naming migration evidence

Date: 2026-09-17. Baseline: clean worktree on
`feature/coread-v0.1-annotations`, HEAD
`fec088c7e62cf60f0545a88461e226900f92c622`, verified before implementation.

## Scope and actual file inventory

The physical project directory is unchanged. No formal Tunnel profile, DPAPI file,
8081 instance or iCloud service was accessed or changed. Only isolated test API
children and browsers were started/stopped. No user-database annotations were
created. Browser tests explicitly read a real seven-page Chinese paper's PDF bytes
and uploaded them into a temporary database; the existing user database was not
used as a fixture.

| Files | Actual change |
| --- | --- |
| `src/caeliae_read/__init__.py` | New main-package identity, moved from legacy package |
| `src/caeliae_read/config.py` | New variables plus one-version legacy parsing; conflict failure and stderr-only warnings |
| `src/caeliae_read/api/__init__.py`, `api/app.py`, `api/schemas.py` | Moved implementation; imports/service identity/title/health/launch module updated; public schemas unchanged |
| `src/caeliae_read/application/__init__.py`, `application/service.py` | Moved sole service; class/import names changed, business semantics unchanged |
| `src/caeliae_read/domain/__init__.py`, `domain/models.py`, `domain/public_schemas.py` | Moved sole models/schemas, no annotation schema changes |
| `src/caeliae_read/storage/__init__.py`, `storage/repository.py`, `storage/migrations.py` | Moved sole repository/migrations; class identity changed, SQL/migrations unchanged |
| `src/caeliae_read/mcp/__init__.py`, `mcp/server.py` | New server identity; correct five-read/one-write instructions; startup configuration validation |
| `src/coread_core/__init__.py`, `mcp/__init__.py`, `mcp/server.py` | Temporary marker packages and module delegate only; other legacy implementation files removed/moved |
| `pyproject.toml` | Distribution, description, two main and two alias console entries |
| `start-caeliae-read-mcp.cmd`, `start-coread-mcp.cmd`, `scripts/start-local.ps1` | Main launch command, thin legacy batch delegation, new API module |
| `scripts/configure-secure-tunnel.ps1`, `scripts/invoke-secure-tunnel.ps1` | Display/error wording only; legacy external profile/key/launcher targets unchanged; scripts not executed |
| `web/package.json`, `web/index.html`, `web/public/manifest.webmanifest` | New brand metadata; no scope/route changes |
| `web/src/App.tsx` | Brand/error wording and successful legacy storage restoration; restore identity/checkpoint together before rendering |
| `tests/conftest.py`, `test_mcp_contract.py`, `list_stdio_tools.py`, `verify_mcp_acceptance.py`, `stdio_create_assistant_annotation.py` | Main-package imports/module/config names; six-tool test naming |
| `tests/audit_stdio_surface.py` | Isolated six-tool audit, schema/hint checks; no assistant write |
| `tests/browser_acceptance.mjs`, `compact_header_acceptance.mjs`, `real_annotation_scroll_acceptance.mjs`, `responsive_layout_acceptance.mjs`, `stdio_web_annotation_acceptance.mjs`, `zoom_remember_revoke_acceptance.mjs` | Main environment/storage/diagnostic names only; existing assertions retained |
| `tests/test_naming_migration.py`, `naming_browser_acceptance.mjs`, `run_naming_regression.py` | Added isolated migration/entry/config/browser regressions |
| `README.md`, `DEVLOG.md`, `docs/provenance.md`, this file | New main-name documentation, provenance and bounded acceptance evidence |

## Entry structure

- `caeliae-read-api` and `coread-api` load the exact same
  `caeliae_read.api.app:main` callable.
- `caeliae-read-mcp` and `coread-mcp` load the exact same
  `caeliae_read.mcp.server:main` callable.
- `python -m coread_core.mcp.server` imports and calls the new `main` only.
- `start-coread-mcp.cmd` calls `start-caeliae-read-mcp.cmd`, which runs the new module.

No old settings, cache, service or database implementation is duplicated. These
old CLI/module/batch entries are deletion candidates after phase 2 external
deployment migration, not permanent compatibility commitments.

## Data-contract evidence

- Database basename remains `coread.sqlite3`, defaults remain `data/`, 8765 and
  loopback. `/reader/`, PWA scope, API routes and all six tool names remain intact.
- The migrated SQL migration file has the same Git blob as baseline:
  `e9de6e1e5cf8cab8231659b93fdd15cb11719756`.
- API request schemas have the same Git blob:
  `a897f99fd8da2a472b216707ed1471dc49108140`.
- Domain models and public schemas are unchanged; service/repository differences
  are import/class names, not annotation, normalized-quad, remember, idempotency or
  SQL semantics.
- Six MCP entry variants (new/old modules, CLIs and batches) returned the identical
  saved context from one isolated SQLite database. Old-variable-only stdio also
  returned that context without corrupting protocol stdout.
- Conflicting resolved DATA_ROOT values fail before either database directory is
  created, for new/old MCP modules and the API module.
- Browser restores the same paper/session/page 2 from legacy keys, writes new
  keys, and retains both legacy keys. Rendering begins only after valid restoration.

## Observed tests

Backend: **34 passed**, two existing dependency deprecation warnings. Frontend:
`pnpm build` passed; existing large-bundle advisory remains. Local stdio initialize,
six-tool list/input/output schema/hints, safe-output audit, both API CLIs, all MCP
entry variants, all seven configuration aliases, priority/warning privacy and
resolved-root conflict tests passed. Build/entry registration used existing
dependencies; no new dependency was downloaded.

Seven isolated real-browser scripts passed:

1. Naming: legacy keys/page restoration, same-anchor user/AI switching and source
   refocus after page navigation.
2. Real annotation scroll: wheel collapse/expand, card open/close, retained active
   annotation, active/settled selection, two polling cycles, mode replacement.
3. Compact header: mid/narrow sizes, drawer/card/selection/pointer guards,
   continuous up/down scroll and viewport-width simulation of sidebar opening.
   This is not a live ChatGPT host-sidebar interaction test.
4. Remember/zoom: 150% and 200% canvas/text-layer/underline/highlight geometry;
   revoke only remember, retain assistant note/underline, reject assistant deletion.
5. Real stdio-to-web: assistant author, remember styling, normalized anchor,
   idempotent replay with one row, automatic polling display.
6. Responsive layout: no desktop list above PDF, compact drawer, slim/upward
   expansion, desktop restoration and preserved page/explicit zoom/scroll.
7. Existing reading acceptance: selected passage persistence, refresh page 2,
   1180/1024/768/390px viewports without document overflow.

| Viewport/state | Header px | Toolbar px | Stage scrollTop |
| --- | ---: | ---: | ---: |
| 800px top | 86 | 52 | 0 |
| 800px reading | 48 | 30 | 200 |
| 800px upward restore | 86 | 52 | 40 |
| 430px top | 64 | 52 | 0 |
| 430px reading | 48 | 30 | 200 |

At 150%, canvas/text layer measured 892.90625 × 1254.328125px; at 200%,
1190.546875 × 1672.4375px. Both offsets were 0/0px and normalized mark geometry
remained within the existing acceptance tolerances. Screenshot inspection confirmed
new branding and compact chrome.

**Not claimed:** card-triggered automatic cross-page navigation was not fully
revalidated in this phase. The test covers manual page navigation and subsequent
source refocus; the existing cross-page branch is retained unchanged. Formal
Secure MCP Tunnel / Work six-tool discovery and assistant write provenance are
also unverified. This commit does not archive v0.1 as fully accepted.

Logs/screenshots are local ignored `.artifacts/naming-migration/`; transient test
databases are removed by the runner. Initial permission/async-save timing failures
were not counted as passes; final code was rebuilt and the full suite rerun.

## Every retained old-name location and reason

Search scope: current source/tests/scripts/metadata/docs plus tracked filenames;
exclude Git history, dependencies, ignored generated artifacts and user data.
The following groups account for every case-insensitive old-brand match outside
this explanatory inventory. This inventory intentionally repeats identifiers for
auditability, not as new product branding.

| Location / retained identifier | Reason for each occurrence |
| --- | --- |
| `pyproject.toml`: `coread-api`, `coread-mcp` | Temporary external CLI aliases |
| `src/coread_core/{__init__.py,mcp/__init__.py,mcp/server.py}` and `start-coread-mcp.cmd` filenames | Exactly the required temporary legacy module/batch entry paths |
| `src/caeliae_read/config.py`: four `COREAD_` lookup/conflict/warning lines | One-version environment compatibility and value-free diagnostics |
| `src/caeliae_read/config.py`: `coread.sqlite3` | Required existing-data compatibility, not branding |
| `src/caeliae_read/application/service.py`: `coread_error` | Existing public API error code; changing it would alter the wire contract |
| `web/src/App.tsx`: both `coread.active*` keys | Successful old browser-state fallback, no old-key deletion |
| `scripts/configure-secure-tunnel.ps1`: profile name, old launcher path, printed actual profile name | Formal external profile and absolute launcher target deferred to phase 2; print reports actual legacy profile rather than inventing a new one |
| `scripts/invoke-secure-tunnel.ps1`: profile name, DPAPI basename, profile-match expression | Preserve existing external deployment/credential identities; no profile execution or decryption this phase |
| `tests/audit_stdio_surface.py`: old DATA_ROOT removal | Isolation from inherited legacy configuration |
| `tests/conftest.py`: database basename | Assert unchanged data naming |
| `tests/conftest.py`: old-brand prefix and page_text | Exact content of unchanged historical selectable PDF fixture, not UI copy |
| `tests/fixtures/selectable-paper.pdf` content/metadata | Immutable historical test resource; changing its bytes would change fixture dedupe/anchors |
| `tests/test_mcp_contract.py`: two old DATA_ROOT removals and old batch filename | Test isolation and required thin-launcher regression |
| `tests/test_naming_migration.py`: old prefix/keys, old module/CLI/batch entries, old database basename | Every occurrence explicitly tests alias priority, stderr privacy, default filename, delegated identity or conflict failure |
| `tests/naming_browser_acceptance.mjs`: legacy paper/session writes and reads | Seed and verify both legacy keys survive successful new-key migration |
| `tests/run_naming_regression.py`: legacy environment-prefix removal | Prevent inherited deployment configuration from affecting isolated tests |
| `README.md`: physical directory, database basename, environment aliases, browser keys and old entry names | Explain actual compatibility/deferred-removal boundaries |
| `README.md`: legacy formal profile and historical Coread Core v0 | Actual unchanged deployment identity and true historical four-tool acceptance |
| `DEVLOG.md`: historical title qualifier, physical directory/database, environment aliases, historical-name note | Explicit history/data compatibility, not retroactive rebranding |
| `DEVLOG.md`: historical `coread-v01-work-remember-20260913` idempotency key | Preserve the real past key, while correcting local-stdio versus formal-Tunnel provenance |
| `docs/provenance.md`: two reference repository URLs and historical name | Required research attribution and honest naming history |
| Current Git branch and past commits | Requested branch continuity; no Git history rewriting |

Built frontend assets use the new brand; the only legacy browser identifiers in
the current bundle are the two necessary storage-fallback keys. Ignored old
build/test artifacts or local installation metadata are not new implementation
sources and were not swept or rewritten. No other implementation is retained for
old-brand consumers.
