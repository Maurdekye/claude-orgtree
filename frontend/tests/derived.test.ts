// derived.test.ts — the state-deduplication programme, kept honest.
//
// `docs/state-architecture-review.md` deleted a long list of second copies of
// server-owned state and replaced them with derivations. Nothing in a running
// app notices when one creeps back: the copy is right for a while, and the bug
// it causes surfaces weeks later as "the panel is stale again". So this suite
// reads the sources and asserts the shapes that were removed are still absent,
// with an ALLOWLIST that has to name a reason for every survivor.
//
// It also carries a drift guard, in the spirit of `backend/tests/msgvis.py`:
// the numbers the behavioural suites assume are re-read here, so changing one
// fails loudly instead of quietly making a test meaningless.
//
// Run:  cd frontend && node tests/run.mjs derived

import './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'

/** injected by tests/run.mjs — the bundle does not sit next to the sources */
declare const __SRC_DIR__: string
const SRC = __SRC_DIR__
const read = (p: string) => readFileSync(path.join(SRC, p), 'utf8')
const uiFiles = ['App.tsx', 'DiskBrowser.tsx', 'picker.tsx', 'forms.tsx',
  ...readdirSync(path.join(SRC, 'canvas')).filter((f) => f.endsWith('.tsx'))
    .map((f) => `canvas/${f}`)]

const lines = (p: string) => read(p).split('\n').map((l, i) => ({ n: i + 1, l }))
/** the file with comment lines removed — this suite asserts on what the code
 *  DOES, and every deleted mechanism here is named in a comment explaining why
 *  it was deleted (`beat()`'s docstring cites `pollWhileBusy` by name) */
const code = (p: string) => read(p).split('\n')
  .filter((l) => !/^\s*(\/\/|\*|\/\*)/.test(l)).join('\n')

// --------------------------------------------------------------------- ①
test('①  no useState is seeded from server data, except where documented',
  () => {
    // P3 collapsed 25 of these into three edit buffers. The shape to catch is
    // `useState(<something read from a prop>)`: it snapshots once at mount and
    // never re-syncs, which is the mechanism behind "the charter looks empty".
    const SERVERISH = /useState(?:<[^>]*>)?\(\s*(?!\)|\[\]|''|""|`|0[,)]|1[,)]|false|true|null|undefined|\{\s*\}|\(\)\s*=>)/
    const PROPISH = /\b(tree|node|chat|box|inbox|scope|base|draft|data|org|m|b|it)\??\./
    const hits: string[] = []
    for (const f of uiFiles) {
      for (const { n, l } of lines(f)) {
        if (SERVERISH.test(l) && PROPISH.test(l)) hits.push(`${f}:${n} ${l.trim()}`)
      }
    }
    // Every entry must be an UNCOMMITTED OPERATION or a one-time proposal —
    // never a mirror of a value the server owns and keeps changing.
    const allow = [
      // DraftScopeModal stages permissions for an agent that does not exist
      // yet: `base` is a proposal, not a live server value (D-32 records the
      // deliberate exception, and re-deriving would overwrite staged edits)
      /canvas\/modals\.tsx:\d+ const \[dirs, setDirs\]/,
      /canvas\/modals\.tsx:\d+ const \[tools, setTools\]/,
      /canvas\/modals\.tsx:\d+ const \[vis, setVis\] = useState\(base\.org_visibility\)/,
      /canvas\/modals\.tsx:\d+ const \[effort, setEffort\] = useState\(base\.effort/,
      // the hire draft's credit slider — same thing, for a node not yet hired
      /canvas\/cards\.tsx:\d+ const \[grant, setGrant\] = useState\(\(\) => \{/,
    ]
    const unexplained = hits.filter((h) => !allow.some((a) => a.test(h)))
    assert.deepEqual(unexplained, [],
      'a new useState-from-a-prop appeared; make it derived, or add it to the '
      + 'allowlist WITH the reason it is an uncommitted edit and not a mirror')
  })

// --------------------------------------------------------------------- ②
test('②  the conversation is fetched in exactly one place', () => {
  const hits: string[] = []
  for (const f of uiFiles) {
    for (const { n, l } of lines(f)) {
      if (/\bgetChat\(/.test(l)) hits.push(f)
    }
  }
  // desk.tsx LineagePanel reads an ARCHIVED bearer's transcript (an immutable
  // object — §8.3 of the review says a snapshot is correct there); modals.tsx
  // reads `init` once for the model/permission header of a config panel.
  //
  // ⚠ FILES, not file:line. Pinning the line number made this guard fail on
  // any edit ANYWHERE ABOVE the call — it fired on an unrelated change seven
  // lines earlier (2026-08-04) reporting "a live conversation must come from
  // convo.ts", which is not remotely what had happened. A guard that cries
  // wolf on every refactor gets muted, and this one is worth keeping audible.
  assert.deepEqual(hits.sort(), ['canvas/desk.tsx', 'canvas/modals.tsx'],
    'a live conversation must come from convo.ts, not a private getChat')
  assert.equal(hits.length, 2, 'exactly two sanctioned getChat call sites')
  assert.ok(read('convo.ts').includes('getChat(slug, nid, e.s.win)'),
    'convo.ts is still the one that fetches it')
})

// --------------------------------------------------------------------- ③
test('③  liveness is never gated on the data it repairs (D-34)', () => {
  // the bootstrap trap: `poll only while the payload says busy`, where busy
  // arrives in the payload the poll fetches. The gate must be something known
  // locally — a mounted view, an open panel.
  const src = read('convo.ts')
  assert.ok(!/pollWhileBusy/.test(code('convo.ts')),
    'the busy-gated poller stays deleted')
  assert.ok(/if \(e\.poll \|\| !e\.subs\.size\) return/.test(src),
    'the heartbeat is gated on SUBSCRIPTION')
  assert.ok(/cur\.s\.chat\?\.busy \? BUSY_POLL_MS : IDLE_POLL_MS/.test(src),
    'busy still decides the CADENCE — only how often, never whether')
  const shared = read('canvas/shared.ts')
  assert.ok(/export function usePolled/.test(shared),
    'the panel heartbeat (G5) still exists')
  assert.ok(/const t = setInterval\(tick, ms\)/.test(shared),
    'and it is unconditional while mounted')
})

// --------------------------------------------------------------------- ④
test('④  the mutable read panels all poll (G5)', () => {
  // getInbox / getNodeInbox / getEvents / getAudiences / getHistory /
  // getScratch display data that changes while the panel is open.
  const polled = ['getInbox', 'getNodeInbox', 'getEvents', 'getAudiences',
    'getHistory', 'getScratch']
  const bad: string[] = []
  for (const f of uiFiles) {
    const text = read(f)
    for (const fn of polled) {
      const re = new RegExp(`^.*\\b${fn}\\(`, 'gm')
      for (const m of text.match(re) ?? []) {
        if (!/usePolled|folder ===/.test(m)) bad.push(`${f}: ${m.trim()}`)
      }
    }
  }
  assert.deepEqual(bad, [], 'a mutable panel went back to fetching once')
})

// --------------------------------------------------------------------- ⑤
test('⑤  the client-side event accumulations stay deleted (G4)', () => {
  const gone = ['setActivity', 'setPulses', 'setStreams', 'streamEvt']
  const hits: string[] = []
  for (const f of [...uiFiles, 'convo.ts', 'canvas/shared.ts']) {
    for (const { n, l } of lines(f)) {
      for (const g of gone) if (l.includes(g)) hits.push(`${f}:${n} ${g}`)
    }
  }
  assert.deepEqual(hits, [],
    'activity/pulses are derived from the tree payload and the server live tail')
  // and the live tail is the SERVER's, mirrored — never accumulated here.
  // (Mirrored via a per-row normalization, not a cast: LiveRowPayload.text is
  // optional on the wire and LiveRow.text is not — review fix 2026-08-04.)
  const src = read('convo.ts')
  assert.ok(/const live: LiveRow\[\] = \(c\.live \?\? \[\]\)\.map/.test(src),
    'convo.live is a mirror of chat.live, not a local accumulation')
  assert.ok(!/live: \[\.\.\.e\.s\.live/.test(src), 'nothing appends to it locally')
})

// --------------------------------------------------------------------- ⑥
test('⑥  a wide table still scrolls inside its own message (D-14)', () => {
  const css = readFileSync(path.join(SRC, 'styles.css'), 'utf8')
  const rule = /\.md table \{[^}]*\}/.exec(css)?.[0] ?? ''
  assert.ok(rule, '.md table rule is gone')
  for (const decl of ['display: block', 'max-width: 100%', 'overflow-x: auto']) {
    assert.ok(rule.includes(decl), `.md table lost \`${decl}\``)
  }
})

// --------------------------------------------------------------------- ⑦
test('⑦  drift guard: the constants the behavioural suites assume', () => {
  const src = read('convo.ts')
  const want: [RegExp, string][] = [
    [/export const CHAT_WINDOW = (\d+)/, '120'],
    [/export const MAX_WINDOW = (\d+)/, '1000'],
    [/const BUSY_POLL_MS = (\d+)/, '2500'],
    [/const IDLE_POLL_MS = (\d+)/, '7000'],
    [/const NUDGE_MS = (\d+)/, '200'],
    [/const COPIES_WINDOW = (\d+)/, '200'],
    [/const COPIES_NEEDLE = (\d+)/, '200'],
  ]
  for (const [re, v] of want) {
    const m = re.exec(src)
    assert.ok(m, `${re} no longer matches convo.ts`)
    assert.equal(m![1], v,
      `${re} changed — re-read frontend/tests/convo.test.tsx before trusting it`)
  }
  assert.equal(/const MAIL_WINDOW = (\d+)/.exec(read('canvas/mail.tsx'))?.[1], '40')
  assert.ok(/scrollTop < 240 && hasOlder/.test(read('canvas/desk.tsx')),
    'the chat still pages on scroll (D-56)')
  assert.ok(/scrollHeight - el\.scrollTop - el\.clientHeight < 240/.test(read('canvas/mail.tsx')),
    'the mail list still pages on scroll (D-56)')
})

// --------------------------------------------------------------------- ⑧
test('⑧  nothing on screen is retired on a clock', () => {
  // D-50's rule: superseded is not replaced. The 5-second expiry and the
  // 300-character prefix match that used to drop live rows are gone, and the
  // scaffolding now retires only in the patch that installs its replacement.
  const src = code('convo.ts')
  assert.ok(!/_at < 5000|Date\.now\(\) - r\._at/.test(src), 'no live-row expiry timer')
  assert.ok(!/startsWith\(r\.text\.slice/.test(src), 'no prefix matching')
  assert.ok(/const retire: Partial<Convo> = \{\}/.test(src),
    'retirement still happens in one atomic patch with the payload')
  const desk = code('canvas/desk.tsx')
  assert.ok(!/setInterval/.test(desk),
    'the desk owns no timer of its own — the store does')
})

// --------------------------------------------------------------------- ⑨
test('⑨  every read-marking call refreshes the payload its rows come from',
  () => {
    // TWO user bug reports, one defect, 2026-08-07 and 2026-08-08: "marking
    // mail as read takes several seconds". The server answers in ~5 ms both
    // times. The rows come from getInbox (a 5 s usePolled) or from the tree
    // prop (a 6 s heartbeat), and a read call that refreshes neither leaves
    // the mark sitting there until an unrelated tick.
    //
    // ⚠ The server's `changed` broadcast does NOT rescue it: that makes
    // clients refetch the TREE, and the user-inbox rows are not in the tree.
    // That is exactly why the per-mail path had to bump its own dep — and why
    // fixing it did not fix "mark all read", a SIBLING CALL SITE I missed.
    // One missed sibling is the whole reason this is a family guard and not
    // another per-case test: it fails on the next one too.
    const CALLS = /\b(markRead|clearInbox|orgInboxRead)\s*\(/g
    // documented exception: the modal-CLOSE acknowledgement in OrgCanvas. Its
    // only visible effect is the canvas tile's badge, which IS tree-backed —
    // so the server's broadcast genuinely does refresh it.
    const EXEMPT = [/if \(tree\.org_inbox\?\.unread\) orgInboxRead/]
    const bad: string[] = []
    for (const f of ['App.tsx', 'canvas/mail.tsx', 'canvas/OrgCanvas.tsx']) {
      for (const { n, l } of lines(f)) {
        if (/^\s*(\/\/|\*)/.test(l)) continue
        if (!CALLS.test(l)) { CALLS.lastIndex = 0; continue }
        CALLS.lastIndex = 0
        if (/^\s*(export )?const (markRead|clearInbox|orgInboxRead)/.test(l)) continue
        if (EXEMPT.some((re) => re.test(l))) continue
        // the refresh may ride the same line or the next two (formatting)
        const near = lines(f).filter((x) => x.n >= n && x.n <= n + 3)
          .map((x) => x.l).join(' ')
        if (!/\.then\(/.test(near)) {
          bad.push(`${f}:${n}  ${l.trim().slice(0, 80)}`)
        }
      }
    }
    assert.deepEqual(bad, [],
      'a read-marking call with no .then() refresh — its rows will keep their '
      + 'unread mark until the next poll:\n  ' + bad.join('\n  '))
  })

// --------------------------------------------------------------------- ⑩
test('⑩  no destructive op fires straight off a click — each one asks first',
  () => {
    // User bug 2026-08-09: "retire on desk view has no confirmation". It was
    // the ONE seat-freeing action wired directly to onClick, sitting next to
    // a dissolve button that asks — and a mis-click stops an agent mid-turn,
    // with the undo living in a toast that scrolls away.
    //
    // Three call sites had it (the desk action bar, the ⚙ panel, the lineage
    // bearer row) and only the reporter's route was named. That is the same
    // shape as the read-marking miss in ⑨: fix the family, not the instance.
    //
    // The rule: an op whose effect is to STOP or DESTROY something reaches
    // `op({op: ...})` from a ConfirmModal's onConfirm, never from an onClick
    // handler. Detected structurally — an onClick line that also carries the
    // op call is the defect; a setAsking/setRetiring hand-off is the fix.
    const DESTRUCTIVE = /op\(\{\s*op:\s*'(retire|dissolve|delete)'/
    const bad: string[] = []
    for (const f of ['canvas/desk.tsx', 'canvas/modals.tsx', 'canvas/cards.tsx',
      'canvas/OrgCanvas.tsx', 'App.tsx']) {
      for (const { n, l } of lines(f)) {
        if (/^\s*(\/\/|\*)/.test(l)) continue
        if (DESTRUCTIVE.test(l) && /onClick/.test(l)) {
          bad.push(`${f}:${n}  ${l.trim().slice(0, 90)}`)
        }
      }
    }
    assert.deepEqual(bad, [],
      'a destructive op fires directly from a click, with no confirmation:\n  '
      + bad.join('\n  '))
  })
