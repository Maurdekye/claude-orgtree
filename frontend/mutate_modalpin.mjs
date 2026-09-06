// mutate_modalpin.mjs — do the pinned-modal checks actually catch anything?
//
// Every check in `tests/modalpin.test.tsx` claims one behaviour of
// `src/canvas/modalpin.tsx`. This harness breaks that behaviour, one mutant at
// a time, and requires the NAMED check to be the one that goes red. A mutant
// the suite still passes means the check is decoration — which is the failure
// mode this repo cares about most: a guard that reads correctly, runs, reports
// success and means nothing.
//
// The load-bearing one is `pin-remounts-the-subtree`. Nesting the children one
// level deeper when pinned looks IDENTICAL in a screenshot and in every
// class-name assertion, and it silently throws away the surface's scroll
// position, its open row and its half-typed reply. §3 is the only thing
// standing between that mistake and the user, so it had better fail here.
//
//   node mutate_modalpin.mjs                 (from frontend/) — all of them
//   node mutate_modalpin.mjs 0 6             — mutants [0,6), for chunking a
//                                              run that takes ~100 s each
//
// ⚠ RUN IT IN A WORKTREE. It rewrites a tracked source file in place (restored
// from the exact bytes read at startup, CRLF preserved), and a crash mid-run
// leaves the mutant on disk.
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync } from 'node:fs'

const FILE = 'src/canvas/modalpin.tsx'

const MUTANTS = [
  {
    // the whole point of the file: same element, same children, re-dressed
    name: 'pin-remounts-the-subtree',
    from: `        {children}`,
    to: `        {pinned ? <div className="regression-wrapper">{children}</div> : children}`,
    mustFail: '§3',
  },
  {
    name: 'handles-render-before-the-children',
    from: `        {children}
        {/* AFTER the children on purpose`,
    to: `        {pinned && EDGES.map((edge) => (
          <div key={'pre-' + edge} className={'modalpin-rs ' + edge} />
        ))}
        {children}
        {/* AFTER the children on purpose`,
    mustFail: '§3',
  },
  {
    name: 'escape-closes-a-pinned-window',
    from: `  useEsc(useCallback(() => { if (!pinned) esc() }, [pinned, esc]))`,
    to: `  useEsc(esc)`,
    mustFail: '§4',
  },
  {
    name: 'the-backdrop-still-closes-when-pinned',
    from: `      onClick={pinned || !backdropClose ? undefined
        : (e) => { e.stopPropagation(); close() }}`,
    to: `      onClick={!backdropClose ? undefined
        : (e) => { e.stopPropagation(); close() }}`,
    mustFail: '§4',
  },
  {
    name: 'closing-a-pinned-window-also-unpins-it',
    from: `              onClick={(e) => { e.stopPropagation(); close() }}>
              <CloseIcon fontSize="inherit" />`,
    to: `              onClick={(e) => { e.stopPropagation(); unpinModal(kind); close() }}>
              <CloseIcon fontSize="inherit" />`,
    mustFail: '§4',
  },
  {
    name: 'the-panel-stops-swallowing-its-own-clicks',
    from: `        onClick={(e) => { onPanelClick?.(e); e.stopPropagation() }}`,
    to: `        onClick={(e) => { onPanelClick?.(e) }}`,
    mustFail: '§4b',
  },
  {
    name: 'geometry-commits-on-every-pointermove',
    from: `    if (!g.moved) return
    setLive(gestureRect(g, e))`,
    to: `    if (!g.moved) return
    commitModalRect(kind, gestureRect(g, e))
    setLive(gestureRect(g, e))`,
    mustFail: '§5',
  },
  {
    name: 'no-movement-threshold-so-a-click-repositions',
    from: `    g.moved ||= Math.hypot(e.clientX - g.sx, e.clientY - g.sy) >= 3`,
    to: `    g.moved = true`,
    mustFail: '§5',
  },
  {
    name: 'escape-does-not-cancel-a-drag',
    from: `      if (e.key === 'Escape' && gesture.current) {`,
    to: `      if (false && gesture.current) {`,
    mustFail: '§5',
  },
  {
    name: 'shrinking-from-the-west-walks-the-window-east',
    from: `    if (w < PIN_MIN_W) { if (g.edge.includes('w')) x = g.o.x + g.o.w - PIN_MIN_W; w = PIN_MIN_W }`,
    to: `    if (w < PIN_MIN_W) { w = PIN_MIN_W }`,
    mustFail: '§5b',
  },
  {
    name: 'a-pin-is-not-persisted',
    from: `    if (Object.keys(next).length) localStorage.setItem(MODAL_PINS_KEY, JSON.stringify(next))
    else localStorage.removeItem(MODAL_PINS_KEY)`,
    to: `    if (!Object.keys(next).length) localStorage.removeItem(MODAL_PINS_KEY)`,
    mustFail: '§1',
  },
  {
    name: 'raise-does-not-renormalise',
    from: `  write(renorm(pins, kind))
}
/** geometry commits ONCE per gesture`,
    to: `  write(pins)
}
/** geometry commits ONCE per gesture`,
    mustFail: '§1',
  },
  {
    name: 'the-z-band-is-not-clamped',
    from: `  Math.min(MODAL_Z_TOP, MODAL_Z_BASE + Math.max(0, z))`,
    to: `  MODAL_Z_BASE + z`,
    mustFail: '§1',
  },
  {
    name: 'a-garbage-entry-is-trusted',
    from: `          if (o && isRect(o.rect) && typeof o.z === 'number' && Number.isFinite(o.z)) {`,
    to: `          if (o) {`,
    mustFail: '§1',
  },
  {
    name: 'the-commit-is-not-clamped-to-the-window',
    from: `  write({ ...pins, [kind]: { ...pins[kind]!, rect: clampRect(rect, winSize()) } })`,
    to: `  write({ ...pins, [kind]: { ...pins[kind]!, rect } })`,
    mustFail: '§1b',
  },
  {
    name: 'an-unmeasurable-panel-pins-at-zero-by-zero',
    from: `  if (!r || r.width <= 0 || r.height <= 0) return MODAL_FALLBACK_RECT`,
    to: `  if (!r) return MODAL_FALLBACK_RECT`,
    mustFail: '§1c',
  },
  {
    name: 'a-jump-closes-a-pinned-panel-too',
    from: `export const closeIfCentred = (kind: string, close: () => void): void => {
  if (!isModalPinned(kind)) close()
}`,
    to: `export const closeIfCentred = (kind: string, close: () => void): void => {
  close()
}`,
    mustFail: '§6b',
  },
  {
    name: 'a-pointerdown-in-a-window-does-not-raise-it',
    from: `        onPointerDown={pinned ? () => raiseModal(kind) : undefined}>`,
    to: `        onPointerDown={undefined}>`,
    mustFail: '§6',
  },
  {
    // ⚠ THE DEFECT ITSELF: one component, two mount sites, ONE pin identity.
    // The canvas reader and the docket reader are then the same window — same
    // rect, same z, neither movable or raisable apart from the other (found in
    // a real browser by codex-delivery, 2026-09-06). Ignoring the prop is
    // exactly what the code did before the fix.
    file: 'src/canvas/docs.tsx',
    name: 'both-readers-share-one-pin-identity',
    from: `    <PinFrame kind={pinKind} title={doc?.title ?? 'document'}`,
    to: `    <PinFrame kind="doc" title={doc?.title ?? 'document'}`,
    mustFail: '§7',
  },
]

// Normalise to LF, mutate, write CRLF back, restore the original bytes.
// ⚠ A MUTANT MAY NAME ITS OWN FILE (`file:`), because not every mutation of
// this feature lives in modalpin.tsx — the two-readers pin identity is a PROP
// one component passes, in docs.tsx. Every file any mutant touches is read
// once here and restored from those exact bytes, CRLF preserved, by the same
// `finally` that always covered modalpin.tsx.
const FILES = [...new Set([FILE, ...MUTANTS.map((m) => m.file ?? FILE)])]
const RAW = new Map(FILES.map((f) => [f, readFileSync(f, 'utf8')]))
const SRC = new Map(FILES.map((f) => [f, RAW.get(f).split('\r\n').join('\n')]))
const restore = () => { for (const [f, raw] of RAW) writeFileSync(f, raw) }
const writeMutant = (f, text) => writeFileSync(f, text.split('\n').join('\r\n'))

function runSuite() {
  try {
    return execFileSync('node', ['tests/run.mjs', 'modalpin'],
      { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] })
  } catch (e) {
    return (e.stdout ?? '') + (e.stderr ?? '')
  }
}

console.log('baseline ...')
const base = runSuite()
const bm = base.match(/^# pass (\d+)/m) ?? base.match(/pass (\d+)/)
if (/^✖/m.test(base) || !bm) {
  console.log('BASELINE IS NOT GREEN — every result below would be meaningless.')
  console.log(base.slice(-2000))
  process.exit(2)
}
console.log(`  baseline: ${bm[1]} passed, 0 failed`)

const from = Number(process.argv[2] ?? 0) || 0
const to = process.argv[3] ? Number(process.argv[3]) : MUTANTS.length
const RUN = MUTANTS.slice(from, to)
if (RUN.length !== MUTANTS.length) console.log(`  (mutants ${from}..${to - 1} of ${MUTANTS.length})`)

let bad = 0
try {
  for (const m of RUN) {
    const f = m.file ?? FILE
    const src = SRC.get(f)
    if (!src.includes(m.from)) {
      console.log(`  ! ${m.name}: target text not found in ${f} — THIS HARNESS IS STALE, fix it`)
      bad++
      continue
    }
    writeMutant(f, src.replace(m.from, m.to))
    const out = runSuite()
    restore()
    const failed = [...out.matchAll(/^✖ (§\S+ .+?) \(/gm)].map((x) => x[1])
    const hit = failed.filter((f) => f.startsWith(m.mustFail))
    if (failed.length === 0) {
      const broke = /error|Error/.test(out) && !/^✔/m.test(out)
      console.log(`  SURVIVED ${m.name}: the suite still passed`
        + `${broke ? ' (or never ran — check the output)' : ''}`
        + ` — ${m.mustFail} does not test this`)
      bad++
    } else if (hit.length === 0) {
      console.log(`  MISDIRECTED ${m.name}: red, but not on ${m.mustFail} — ${failed}`)
      bad++
    } else {
      console.log(`  killed   ${m.name}  ->  x ${hit[0].slice(0, 72)}`)
    }
  }
} finally {
  restore()
}
console.log(bad
  ? `\n${bad} mutant(s) not properly killed`
  : `\n${RUN.length} of ${RUN.length} mutants killed by the named check`)
process.exit(bad ? 1 : 0)
