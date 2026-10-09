# Caeliae Read

Caeliae Read is a local-first reading workspace in the CAELIAE system. It keeps
papers, reading state, annotations, vocabulary, and Reading workflow records on
the local machine. The HTTP API, browser reader, and optional local MCP server
share one Python service and one SQLite database.

This repository is a public-preview engineering project, not a hosted service.
It does not upload papers or user records to a cloud service by default.

## Product surface

The reader is organized around three areas:

- **Papers** — local PDF library, page state, selections, underline/highlight
  annotations, sticky notes, Summary Notes (`大意`), and Annotation Index.
- **Reading** — Reading tasks, submissions, answers, lifecycle state, and
  feedback.
- **Vocabulary** — vocabulary CRUD, source context, search, JSON/CSV export,
  and local Study/Review.

The current release also includes an optional local stdio MCP integration for
scoped Reader and Reading capabilities. It is not required to use the browser
application.

## Requirements

The repository is developed and tested with:

- Windows with PowerShell and loopback access
- Python 3.12.x
- Node.js 20+ and pnpm 11.19.0

The formal local launcher expects the checkout directory to be named
`caeliae-read` and starts the API on `127.0.0.1:8765`.

## Install and start

From the repository root in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
Set-Location .\web
pnpm install
pnpm build
Set-Location ..
.\start-local.cmd
```

The formal launcher is the only normal API entry point. It starts the local
service, waits for `/readyz`, and opens the Reader.

Local URLs:

- Reader: <http://127.0.0.1:8765/reader/>
- Vocabulary: <http://127.0.0.1:8765/reader/vocabulary.html>
- Reading: <http://127.0.0.1:8765/reader/reading.html>
- API health: <http://127.0.0.1:8765/api/health>

Stop the local API from the launcher window with `Ctrl+C`. Do not terminate
processes by name or delete the local data directory.

## Local data and privacy

By default, the service stores data under `data/`, including the compatibility
database filename `data/coread.sqlite3` and local paper files under
`data/papers/`. The entire data directory is ignored by Git, as are SQLite
WAL/SHM files, local logs, backups, browser state, build output, and temporary
acceptance artifacts.

Do not commit real PDFs, annotations, notes, vocabulary, databases, credentials,
or logs. The tracked PDF under `tests/fixtures/` is synthetic acceptance data.

To use a separate local store, configure the API and MCP process consistently:

```powershell
$env:CAELIAE_READ_DATA_ROOT = Join-Path $env:TEMP ("caeliae-read-" + [guid]::NewGuid())
Remove-Item Env:COREAD_DATA_ROOT -ErrorAction SilentlyContinue
```

The `COREAD_*` variables, `coread.sqlite3`, and legacy entry points remain only
as compatibility surfaces for existing installations. They are not the current
product name or a second implementation.

## Optional MCP integration

Start the local stdio server from another process when MCP access is needed:

```powershell
.\.venv\Scripts\python.exe -m caeliae_read.mcp.server
# Compatibility delegates remain available:
# .\.venv\Scripts\python.exe -m coread_core.mcp.server
# .\start-caeliae-read-mcp.cmd
```

The current local surface contains 11 tools:

1. `list_papers`
2. `get_current_reading_context`
3. `get_current_selection`
4. `get_checkpoint`
5. `list_annotations`
6. `create_assistant_annotation`
7. `list_reading_tasks`
8. `get_reading_task`
9. `create_reading_task`
10. `get_reading_submission`
11. `create_reading_feedback`

The MCP server is local and scoped: it does not provide unrestricted file
access, command execution, arbitrary SQL, PDF upload, or a generic HTTP proxy.
The old `coread_core` Python module and `coread-core` Secure Tunnel profile are
intentionally retained for compatibility. Work connector metadata refresh is
platform-dependent and is not a universal product guarantee.

The optional Secure Tunnel helper scripts require the tunnel client path to be
provided explicitly with `-TunnelClientPath`, or through the
`CAELIAE_TUNNEL_CLIENT` environment variable. For example:

```powershell
$env:CAELIAE_TUNNEL_CLIENT = 'C:\path\to\tunnel-client.exe'
.\start-secure-tunnel.cmd
```

The helpers do not search the filesystem or create an alternate Tunnel profile.

## Development and verification

Backend tests use temporary data roots and do not require the real `data/`
directory:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .artifacts\pytest-temp
```

The repo-local base temporary directory keeps test artifacts isolated from
system temporary-directory permissions and is ignored by Git.

Build the browser client:

```powershell
Set-Location .\web
pnpm build
Set-Location ..
```

Browser acceptance scripts require an explicit target URL. They refuse to
default to the formal runtime. Use an isolated API and data root for write
acceptance, for example:

```powershell
$env:CAELIAE_READ_ACCEPTANCE_URL = "http://127.0.0.1:8771"
$env:CAELIAE_READ_DATA_ROOT = Join-Path $env:TEMP ("caeliae-acceptance-" + [guid]::NewGuid())
node tests\acceptance_guard_test.mjs
node tests\browser_acceptance.mjs
```

Live `8765` acceptance is an explicit, exceptional operation and requires
`CAELIAE_READ_ALLOW_LIVE=1`. It must never be used for write-oriented tests
against personal data by accident.

## Known issues

- A long downward text selection may occasionally stop earlier than intended.
- Text that is already annotated cannot always be reselected for another
  selection action.
- The ChatGPT Work connector's Refresh/metadata behavior depends on the
  platform-side importer and should not be interpreted as a product API failure.

## Architecture and naming

CAELIAE is the overall system name; **Caeliae Read** is the display module;
`caeliae-read` is the repository/engineering identifier; and `caeliae_read` is
the Python package. The service layers are shared by the browser API and MCP
adapter, with migrations kept in source control.

Historical documents may mention Coread Core or Echo Thread when recording the
real migration history. The following compatibility identifiers remain
deliberately unchanged: `coread_core`, the `coread-core` Tunnel profile,
`coread.sqlite3`, `COREAD_*` environment variables, and legacy launchers.

## License

Caeliae Read is licensed under the [MIT License](LICENSE).
Copyright (c) 2026 Elena Cai.

## Release status

The current line is a public-preview candidate. It is intentionally not labeled
1.0. Suggested first release metadata is documented in the release-preparation
notes; creating a GitHub repository, changing visibility, tagging, and pushing
remain explicit maintainer actions.
