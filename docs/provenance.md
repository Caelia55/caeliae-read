# Caeliae Read provenance and research sources

Recorded 2026-09-17, following the read-only source audit of the pre-migration
repository at `fec088c7e62cf60f0545a88461e226900f92c622`.

## Research references

- [meowmana/coread](https://github.com/meowmana/coread)
- [joyceslcl/coread-reading-room](https://github.com/joyceslcl/coread-reading-room)

The latter project's THIRD_PARTY_NOTICES declares derivation from the former.
That declaration describes those two repositories, not this repository.

The audit did not establish that this local project was a clone, fork, or source
derivative of either reference. Available Git history and implementation evidence
support an independent rebuild in the same product problem domain. The product
idea of human/AI co-reading, shared annotations, and MCP informed research;
no upstream source inheritance was found. This is an evidence-bounded conclusion,
not a guarantee about material that may be discovered later.

## Implementation distinctions

This project uses Python/FastAPI/SQLite and a PDF.js reader with normalized
multi-quad PDF anchors. The reference projects use Node/React with TXT/EPUB and
paragraph-index/offset anchoring. Local implementation includes server-side
assistant/user permissions, separate assistant remember semantics and revocation,
idempotent annotation writes, stdio-to-browser polling, PDF source/card focus,
zoom geometry, and compact header/toolbar state machines.

No reference-project MIT license or copyright is inherited merely because product
concepts overlap. If actual upstream snippets are found or introduced later,
record their exact files and origin, and retain the applicable MIT license,
copyright, and attribution for that material. Dependency licenses remain their
own licenses; this document does not replace dependency-license obligations or
grant a license for the local project's original code.

## Naming and acceptance boundaries

Coread Core is the historical local name; the independent module is now Caeliae
Read, within the CAELIAE personal AI environment. Persona 沈渡 is not renamed.
Historical records are not rewritten to pretend the new name existed earlier.

Local six-tool stdio validation remains distinct from formal Secure MCP Tunnel /
Work validation. The final acceptance evidence now records the refreshed Work
six-tool surface and the Work → `coread-core` Secure Tunnel → new-directory
stdio association. The historical four-tool Work record and the earlier local
stdio assistant write remain distinct historical events; neither is rewritten.
The tunnel-client did not emit per-request dispatcher lines, so the current
source conclusion is association evidence rather than per-request log evidence.
