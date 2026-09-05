# Redacted failure fixtures

`backend/orgtree/failfix.py` · `backend/orgtree/failclass.py` · `tools/replay_failure.py` · `backend/tests/test_failure_fixtures.py`

Docket item `redacted-failure-fixtures-and-extensive-logging`. This document is the contract (v2, after the coordinator's review of 2026-09-05T19:52Z). The first path — the Claude/OpenRouter lane through the fake CLI — is what is built and measured; the Codex projection is captured but not yet replayed, and the "extensive logging" half is a later milestone on the same item.

## The problem

A failed turn leaves a 400-character sentence: `turn_error_log[nid]` rows written through `supervisor._for_the_record`. Reproducing a misclassified failure — a dropped connection read as terminal, a 403 read as a usage limit — meant re-creating it live, on a paid lane, against the operator's data. The raw journals that would show it (`journals/projects/<org>/<sid>.jsonl`) contain prompts, mail bodies and, at the boundary, credentials.

## What a fixture is

A small JSON file under `<ORGTREE_DATA>/failfix/<org>/<node>/`, written fail-open at the supervisor's existing recording sites. It is an **allowlist**: a field not named below does not exist, whatever the inputs carried.

```
schema      2
lane        claude | openrouter | codex | other
site        terminal | exhausted | codex        (which recording site wrote it)
at          ISO second, UTC
observed    what the boundary SAW — exit_code, parked, exit_only, started,
            boundary, errors_n, is_error, api_error_status, stream_status,
            stream_code (CLI typed vocabulary or "other"), terminal_reason
            (closed vocabulary or "other"), run, exhausted
tags        per text input (err_blob, stderr_tail, result_detail): the
            RECOGNISED DIAGNOSTIC TAGS found in it, nothing else
lens        per text input: its length
recorded    what the site DECIDED — limit, net, filtered, typed, verdict
codex       an INPUT projection of codex_route.classify_failure — status,
            rpc_code, error_code (identifier or "other"), items_seen,
            had_usage, text_len, pool, served, usage_prose, now — plus the
            recorded outputs kept apart by name (kind_recorded,
            rejected_recorded); null on the other lanes
ran_as      ambient | api-key | openrouter | account | ""   (a sentinel)
cli         exit; version only when it matches N.N.N
phase       an INFERENCE from `observed` (below), "unknown" allowed
```

### No prose

Free text never enters a fixture. From each text input `tags_of` keeps only:

- the classifiers' own phrase vocabularies (`LIMIT_WORDS`, `NET_WORDS`, `FILTER_WORDS` — the suite asserts, by AST over `failclass.py`, that every literal a predicate searches for is in the vocabulary);
- the CLI's typed API-error codes and provider machine tags (`CODE_WORDS`);
- a few fixed diagnoses orgtree itself writes (`DIAG_WORDS`: `the cli exited`, `unknown option`, `enospc`…);
- numbers as typed tags: `status:401`, `exit:1`, `rpc:-32000`, `option:--effort`.

An error that echoes a confidential sentence with no key-shaped pattern in it is therefore **dropped, not scrubbed** — there is no regex that recognises a secret sentence, so the design does not try. The suite plants canary sentences in every capture field (text inputs, stream code, terminal reason, codex status/pool/code, ran_as, version, at) and asserts no five-letter word of them survives, beside diagnostic controls that must.

### Validated leaves

Every string leaf is either a closed vocabulary (`STREAM_CODES`, `TERMINAL_REASONS`, `CODEX_STATUS`, `CODEX_POOLS`, `LANES`, `SITES`), a strict identifier (`_codeword`), or a strict pattern (`_version`, `at`). Anything else becomes `"other"` or `null`. The fixture carries no org, node, account, host, URL or path: the directory it sits in is the only correlation, and that directory is inside the operator's own data root.

### Observed, recorded, inferred

`observed` is fact. `recorded` is the site's decision at the time. `phase` is inferred from `observed` and says so:

| phase | evidence required |
|---|---|
| `admission` | nothing observed before a typed 401/402/429 (claude lanes); the provider's own recorded rejection, or an RPC error before any item (codex) |
| `stream` | output began and no result boundary was reached |
| `result-error` | the boundary was reached and it carried the error (`is_error` / a typed status) |
| `teardown` | the boundary was reached clean and the process still exited nonzero |
| `unknown` | everything else — "no output" does **not** prove nothing ran; a late Codex timeout is not teardown |

### Bounds and safety

- ≤ 4 KB per fixture (`CAP_BYTES`), ≤ 40 per node (`RING`, oldest evicted); tag lists capped.
- Never in the org document: no schema, SQLite/JSON or rollback surface; an older build ignores the directory.
- `record` never raises. It runs after every classifier, reads `err_blob` and never writes it (rule 1 of `_for_the_record` kept), and cannot change a turn's outcome — the suite proves an unwritable root and malformed inputs record nothing and raise nothing.

## Replay

```
python tools/replay_failure.py <fixture.json> [more …] [--assert]
```

Re-runs the predicates on the fixture's tags (`blob_of(tags)`) and observed facts and prints, per fixture, the **recomputed** verdict beside the **recorded** one and their `drift`. `--assert` exits 1 on any drift: a classifier edit is checked against every committed fixture. It is a drift detector, not a statement of which branch the supervisor would take — that also depends on the retry counter, the lane policy and a manual pause, which a fixture does not hold.

The replay equivalence it rests on — `predicate(blob) == predicate(blob_of(tags_of(blob)))` — is asserted over a corpus in the suite; a phrase added to a predicate without being added to the vocabulary fails that check.

### `failclass.py` — the side-effect-free boundary

Importing a pure function from `supervisor` still executes the supervisor's imports, which bind the storage root. `failclass.py` holds **verbatim copies** of the seven pure predicates (`_strict_http_status`, `_typed_status_field`, `_typed_api_status`, `_looks_like_usage_limit`, `_looks_like_connection_failure`, `_died_in_flight`, `_looks_like_filtered`) and imports only `typing`. The suite asserts each copy is byte-identical to the supervisor's function (AST source segments) and answers identically over the corpus and the shape grid, and runs the tool under an import hook that refuses `orgtree.store`/`supervisor`/`ledger`/`codex_route`, `subprocess`, `socket`, `http`, `urllib`, `sqlite3`, `threading` and every file write — with a control proving the hook refuses `orgtree.store` and a write when asked. The supervisor keeps its own copies until the rewiring (import from `failclass`) is reviewed.

### Codex, said plainly

The Codex projection is **captured** at the site after `classify_failure` and validated like everything else, but **not replayed** in this milestone: `codex_route` imports `ledger`, so it is not behind the pure boundary, and `classify_failure` also reads rate-limit snapshots and the account board, which the projection does not carry. `kind_recorded`/`rejected_recorded` are the site's outputs, named as such, and are never fed back as inputs.

## Production integration (held for review)

Three fail-open sites in `supervisor.py`: `_failfix_record` (a helper beside `_for_the_record`) called at the retry-exhausted raise and the terminal raise in `_run_one_turn`, and an inline block in `_codex_leg_attempt` after `classify_failure`. No new classifier input, no provider traffic, no paid turn, no live data. Fixtures for the suite come only from the fake transports (`fakecli.js` via the `test_limit_freeze` rig); nothing is ever copied from a live journal.

## Evidence (first path, 2026-09-05)

- Suite 25/25 on the branch; on main's supervisor without the sites exactly the six real-failure checks fail (red proof).
- Mutants M1–M8 caught: dropped vocabulary phrase, prose leak into tags, unvalidated leaf, phase inferring admission from silence, a constant recorded verdict, `record` no longer fail-open, replay ignoring the died-in-flight shape, lens keeping text.
- Real failures through the fake CLI: died-in-flight (phase stream, verdict net, replay agrees), is_error 401 with planted secrets and canaries (all absent; `status:401`, `invalid api key`; phase result-error; verdict none), died-with-stderr (`enospc`; verdict none; stays terminal).
- Neighbouring suites green on the branch: limit-freeze, provider-limit-freeze, codex-dispatch, codexrun, op-receipts, retry-receipts.
