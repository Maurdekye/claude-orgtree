# Typing wave — plan and status

User green-light 2026-07-31: introduce type annotations to the Python backend and
migrate the frontend to TypeScript, incrementally. This document is the ratified
scope; check items off as they land.

**What this wave is NOT** (assessed and rejected 2026-07-31):
- No async rewrite of the backend. The synchronous, threaded supervisor is a
  deliberate design around subprocess lifecycles and `DOC_LOCK`; FastAPI runs
  sync endpoints in a threadpool, and at this tool's scale an async supervisor
  would be the highest-risk change for the least gain. `.then()` chains in React
  event handlers are idiomatic and stay.
- No behavior change of any kind. Every step in this wave must be inert at
  runtime — annotations, config, and mechanical file conversion only. Anything
  that would change behavior gets its own commit outside the wave.

## Phase 1 — Python annotations (backend)

- [x] `backend/orgtree/schema.py`: `TypedDict` definitions for the org document
      — the JSON persisted per org (`~/orgtree/orgs/<slug>.json`) whose shapes
      previously lived only in people's heads (see the misleading-reads history).
      Single source of truth; extend it rather than re-deriving dict shapes.
- [x] `pyrightconfig.json` (basic mode, py3.10). Run with `npx pyright` from the
      repo root. Not wired into CI yet — the repo has none; update.ps1 is not a
      gate. `backend/tests` is excluded on purpose: the suite deliberately
      passes wrong shapes to assert `LedgerError`, and its correctness gate is
      running it (`python backend/tests/test_ledger.py`), not checking it.
- [x] Annotate signatures across `ledger.py`, `store.py`, `supervisor.py`,
      `api.py`, and the small modules (`mcptool`, `sandbox`, `externtool`,
      `steer`, `subproxy`). Signatures and module-level constants first; locals
      only where inference fails. `dict[str, Any]` is acceptable where a shape
      is genuinely open — never guess a narrower type than the code proves.
- [x] Gate: `npx pyright` reports no errors in basic mode, and
      `backend/tests/test_ledger.py` passes untouched.

## Phase 2 — TypeScript, seam first (frontend)

- [x] `tsconfig.json` (strict, `allowJs`, `noEmit`) + `typescript` devDependency.
      Vite compiles TS natively; `npm run typecheck` (`tsc --noEmit`) is the gate
      — `vite build` does not typecheck.
- [x] `src/types.ts`: interfaces mirroring `schema.py`'s projection types — the
      tree payload, chat, inbox, org list. The API seam is where silent shape
      drift between backend and frontend actually bites.
- [x] `src/api.js` → `src/api.ts` with typed request/response signatures.
- [ ] Convert leaf files: `forms.jsx`, `icons.jsx`, `picker.jsx`, `main.jsx`.
- [ ] Convert `App.jsx`.
- [ ] Convert `Canvas.jsx` LAST, splitting it into modules as it converts
      (3.9 kLOC, ~40 components — the risk concentration; the prop-threading
      chains are what TS helps most with and where a rushed conversion would
      cause regressions). One module per commit.

## Phase 3 — tighten (optional, needs its own green light)

- [ ] pyright strict mode module-by-module.
- [ ] `noUncheckedIndexedAccess` in tsconfig.
- [ ] Shared codegen for the seam types (schema.py → types.ts) if drift ever
      bites in practice; hand-mirrored until then.
