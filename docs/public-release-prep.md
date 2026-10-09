# Public Release Preparation Notes

This note records repository preparation decisions for a first public preview.
It is not a license grant, legal opinion, GitHub operation, or release tag.

## Dependency and license audit

Direct runtime dependencies are permissive-license projects according to their
package metadata and included notices:

- Python: FastAPI, MCP, Pydantic, pypdf, python-multipart, and Uvicorn.
- Python test tools: pytest and httpx.
- Frontend: React, React DOM, PDF.js distribution, Vite, TypeScript, and the
  React Vite plugin.
- Browser acceptance: Playwright Core.

The local dependency set includes MIT, BSD-family, Apache-2.0, and compatible
permissive licenses. PDF.js, Playwright, and TypeScript carry Apache-2.0 notices;
their notice files remain in the installed dependency tree and should be
reviewed again when packaging a distributable bundle.

Candidate repository licenses:

- **MIT** is the simplest fit for a small permissive application repository.
- **Apache-2.0** adds an explicit patent grant and is compatible with the direct
  dependencies, but requires preserving the longer notice structure.

Human decision required: choose the repository license and add the final
`LICENSE` file. No license was selected or added by this preparation pass.

## Public screenshots

Use only the synthetic acceptance PDF and synthetic records:

1. Reader with an underline/highlight, sticky note, and Summary Note (`大意`).
2. Annotation Index showing the Summary Note filter and compact metadata.
3. Vocabulary list with a source-context detail expanded.
4. Reading task page showing questions and submission/review state.

Do not include real paper text, filenames, annotations, notes, vocabulary,
account identifiers, database paths, or browser profile state. Store approved
images in a deliberately tracked documentation location only after review.

## GitHub metadata proposal

- Repository name: `caeliae-read`
- One-line description: `A local-first PDF reading workspace with annotations, vocabulary, and Reading workflows.`
- Suggested topics: `pdf-reader`, `reading`, `annotations`, `vocabulary`, `local-first`, `mcp`, `python`, `react`
- First release label: `public-preview`

`public-preview` is preferred for the first public appearance because the API,
MCP connector behavior, and product boundaries are still explicitly evolving.
Reserve `v0.2.0` for the point at which package/version metadata and the public
compatibility promise are intentionally aligned. Do not use `1.0` for this line.

## Clean-clone checklist

The repository contains Python and frontend dependency declarations, both lock
files, migrations, synthetic fixtures, startup scripts, and setup instructions.
The following are intentional external prerequisites rather than repository
content:

- Python 3.12.x, Node.js, and pnpm.
- A local Edge/Chromium executable for browser acceptance.
- An explicitly started isolated API/data root for write-oriented acceptance.
- Optional Secure MCP Tunnel profile and current-user credential, never copied
  into the repository.
- A human-selected repository license before public distribution.

No current user database or PDF is required for a clean clone. Acceptance
scripts now refuse implicit live-runtime defaults; `8765` requires explicit
opt-in.
