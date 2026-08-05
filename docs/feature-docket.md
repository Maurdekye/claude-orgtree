# Feature docket

Feature requests the user brings directly to the explorer (chat `93f4cfdd`), logged here as
reported for the implementer to triage. This is an inbox, not an authority: the explorer does not
implement, prioritize, or close anything here — only records it.

Distinct from [`interim-docket.md`](interim-docket.md) (bug fixes/reports kept on the
interim-authority branch) and `DECISIONS.md` (the implementer's decision register, which is where a
request from here ends up once it's been picked up).

Entries are numbered `FR-01`, `FR-02`, … — a separate sequence from `DECISIONS.md`'s `D-`/`F-`
numbering, so the two are never confused.

---

### FR-01 · `/remote-control`, if feasible
> potentially enabling /remote-control? if its feasible

Feasibility unknown — **investigate before scoping**. Open questions: what the slash command
actually does in the pinned CLI; whether it works at all in a headless `-p` session (orgtree already
strips the interactive-only tools, and a command needing a live client would be inert); and what it
would mean for an agent inside a sandbox container. orgtree already has a verbatim slash-command
path (`send_message(command=True)`) that delivers a `/…` as its own user event, so the delivery
mechanism exists if the command itself turns out to be viable.

**INVESTIGATED (implementer, 2026-08-04, against the pinned CLI 2.1.220 — `claude remote-control
--help` + binary strings; no live probe, since starting the server ENROLLS THE DEVICE on the
user's claude.ai account, an account-state change that is the user's to make).** Findings:

- It is not a per-session slash command but a **standalone subcommand**: `claude remote-control`
  runs a *persistent server* in a working directory; you connect from claude.ai/code or the Claude
  mobile app and it spawns/controls sessions there (`--spawn same-dir|worktree|session`, capacity
  32). Requires a logged-in subscription and a one-time workspace-trust acceptance in that dir.
- ☞ **The orgtree-shaped hook exists: `--session-id <id>` resumes a SPECIFIC session.** So "take
  over an agent from my phone" is plausibly: orgtree launches
  `claude remote-control --session-id <agent session_id>` in that agent's scratch dir, the user
  drives the agent's real session from claude.ai, orgtree kills the server on release.
- Constraints found: ① the supervisor must NOT run turns on a remote-controlled session (two
  writers, one session id) — needs a `remote-controlled` node state that parks mail until release;
  ② sandboxed agents are out of scope at first — their session files live in the container and
  the container deliberately never holds the subscription token; ③ unknown whether the server
  runs without a TTY (it reads keys — "press 'w'"), which decides whether orgtree can spawn it
  headless; ④ workspace trust may not have been recorded by `-p` runs.
- Next step if pursued: ONE live experiment (user present, their account): start
  `claude remote-control --session-id …` against a probe org's agent, confirm it appears on
  claude.ai/code, confirm TTY-less spawn works, then scope the UX (a desk button + the parked
  node state).

→ moved from `docs/interim-docket.md` F-02, 2026-08-05, by the explorer on the user's instruction.

---

### FR-02 · the mobile wave
> *(added at the review, 2026-08-04, on the user's instruction: the wave joins the prospective
> features here rather than staying a standing hold in memory)*

**NOT BUILT — held by the user** ("hold off implementing until i give the go ahead",
2026-08-01, re-affirmed after an earlier release). The full spec lives at `docs/mobile-spec.md`
(carrying its own HOLD banner); three live bugs its audit surfaced were split out and already
fixed in the pre-dormancy fix batch (`35ec4eb` + follow-ups), so the spec that remains is purely
layout/interaction work. One open ruling rides with it: the compact-desk question sits in
DECISIONS.md §Open and should be answered before (or as part of) the build.

→ moved from `docs/interim-docket.md` F-08, 2026-08-05, by the explorer on the user's instruction.

---

### FR-03 · present a document to the user (in-page review card)
> need the ability for the agent to present documents to the user. this is different than giving a
> download link: this should be used for presenting plans and other things to them. when doing so, a
> little card should pop out the side of the agent, which when clicked, opens the document up for
> visual review in-page.

*(user request 2026-08-05, relayed via 4f69f83a's session; groundwork theirs. NOT BUILT — queued
behind the F-06 wave.)*

⚠ Not `orgtree_send_file` — that is a DOWNLOAD card (outbox/ + `/file`). This is a READING
surface: a plan reviewed in-page without leaving the canvas.

Groundwork (researcher, 2026-08-05):
- Rendering: the desk already has the markdown renderer (`md()` in `canvas/desk.tsx`) and `.md`
  styling with the D-14 table containment — the reader is mostly plumbing.
- "Pops out the side of the agent" = a card anchored to the NODE on the canvas (the credit ask
  bar's outboard-anchored shape), not a chat-stream row.
- Storage: durable + re-openable ⇒ a per-node `documents` list on the org doc (the `asks` /
  `credit_requests` pattern) — the card derives from the doc and survives reload. The chat stream
  windows at 120 rows and is the wrong home.
- Agent tool: `orgtree_present {title, body (markdown), replaces?}` mirroring `orgtree_ask`'s
  shape — parked, never blocking.

→ moved from `docs/interim-docket.md` F-10, 2026-08-05, by the explorer on the user's instruction.

---

### FR-04 · batched asks — multiple questions in one card
> multiple questions should be askable at once in a batch. see the attached images for how it
> looks in claude code's ui.

*(user request 2026-08-05, with reference screenshots of Claude Code's AskUserQuestion batch
form. NOT BUILT — queued behind the F-06 wave.)*

The reference (from the screenshots): ONE card holding several questions as a **tab strip**
across the top (short headers as tab labels, e.g. `Kind · Area · Images · Handoff`), the active
tab underlined; each tab shows its own question with the usual option rows (+Other); answered
tabs keep their selection when you switch back; a single **`N Submit answers`** bar at the
bottom carrying the answered-count; ✕/Esc cancels the whole batch.

Groundwork:
- `orgtree_ask` grows a `questions: [{question, header, options, multi}]` array form (1–4,
  mirroring the single-question fields; the single form stays and normalizes to a 1-batch).
- One ask entry in the ledger holds the batch; ALL answers travel as ONE user mail (per-tab
  answers labeled by header), driving one turn. Voiding/amending applies to the whole batch.
- AskCard renders the tab strip above the existing option rows (the `ask-tab` chip row is
  already there for the single header — it becomes the strip); submit disabled until every
  non-skipped tab has a selection or free text.

→ moved from `docs/interim-docket.md` F-11, 2026-08-05, by the explorer on the user's instruction.
