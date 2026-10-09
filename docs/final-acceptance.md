# Caeliae Read v0.1 final acceptance

Date: 2026-09-18  
Repository branch: `feature/coread-v0.1-annotations`

## Formal Work and Secure Tunnel

The refreshed formal Work connector exposed exactly these six tools:

1. `list_papers`
2. `get_current_reading_context`
3. `get_current_selection`
4. `get_checkpoint`
5. `list_annotations`
6. `create_assistant_annotation`

The accepted source conclusion is:

> Formal Work → `coread-core` Secure Tunnel → new-directory stdio has been
> confirmed end to end by connection state, the unique write, and its local
> persistence association; the current tunnel-client log level does not provide
> a per-request dispatcher line, so this is not presented as per-request log
> evidence.

The local Codex `coread_core` MCP was disabled during the Work calls. No second
Tunnel profile was used, and the existing `coread-core` profile identity,
Tunnel ID, port, DPAPI material, and iCloud configuration were unchanged.

## Idempotent write and polling

Using isolated synthetic selection data, Work created assistant annotation
`1eb0b852-a5d0-4151-9e69-b37d447b4e5e`. The first call returned
`replayed=false`; replaying the identical payload returned the same annotation
ID with `replayed=true`; the local persistence match count was exactly one.

A separate synthetic polling check created assistant annotation
`e70af97b-2dbf-433c-a04d-22834f29e922`. Without refreshing the page, the reader
displayed the assistant mark/card during the polling interval. The card showed
assistant authorship, remember styling, the note, and no user-delete control.

The earlier `fcc17fcd-8332-4a8a-8c47-5261b6966bc9` event remains documented as
the historical local-stdio write and is not reclassified.

## Visual acceptance boundary

The accepted matrix distinguishes reachable states:

- Desktop content width: complete toolbar remains stable; slim is not required.
- Compact content width: after more than 96px accumulated downward scroll on
  the real `.page-stage`, the toolbar is approximately 30px slim.
- Open annotation card: header and toolbar remain expanded because the card
  guard intentionally pins them open.

Each visual check records the actual content viewport width and compact state;
ChatGPT sidebar open/closed is not used as a substitute for either measurement.

## Safety and retained compatibility

No functional code, database, annotation, Tunnel profile, DPAPI file, iCloud
configuration, or running process was changed for archival documentation.
The physical migration is complete; `.venv.pre-caeliae-read`, migration
backups, the historical `coread-core` profile name, and thin legacy launchers
remain intentionally retained. The old `coread-api`, `coread-mcp`,
`python -m coread_core.mcp.server`, and `start-coread-mcp.cmd` entries are
compatibility shims and deletion candidates after external deployment paths
are migrated.
