// modalpin-probe.tsx — the page `modalpin_probe.py` drives in a real browser.
//
// It renders a REAL pinnable surface — DocGalleryModal, one of the four the
// user named ("presentations") — bundled from ../src with the real stylesheet,
// its document list served by a stubbed `fetch` so nothing here talks to a
// server. Behind it sits one background control, which is how the probe asks
// whether a pinned window really lets clicks through to the page beneath.
//
// A second, plainer surface (`PinFrame` around a `settings` panel) is mounted
// on `?two=1` so the probe can watch two pinned windows coexist and raise past
// each other — the gallery is one-at-a-time in the app, so a second copy of it
// would be a fixture that lies about how the app works.
//
// Nothing here is a test: the assertions live in the .py file. This file only
// has to be an honest host — the real component, the real stylesheet, no
// window management of its own.

import { useState } from 'react'
import { createRoot } from 'react-dom/client'
import '../src/styles.css'
import { DocGalleryModal } from '../src/canvas/gallery'
import { PinFrame } from '../src/canvas/modalpin'

const q = new URLSearchParams(location.search)
const log: string[] = []
;(window as unknown as { __probe: { log: string[] } }).__probe = { log }

// ---- the server the gallery reads. Enough documents that the list SCROLLS:
// the scroll position surviving a pin is one of the things only a browser can
// answer, and a fixture short enough to fit would make that check vacuous.
const DOCS = Array.from({ length: 40 }, (_, i) => ({
  id: `doc-${i}`,
  title: `Document number ${i} — a presented plan with a long enough title to wrap`,
  node: i % 2 ? 'planner' : 'builder',
  at: new Date(Date.UTC(2026, 8, 6, 9, i % 60)).toISOString(),
  evicted: false,
  node_state: 'live' as const,
  tier: 'sonnet',
  body: `# Document ${i}\n\nBody text for document ${i}.\n\n`
    + 'Selectable prose so the probe can drag a selection across it. '.repeat(6),
}))
const json = (v: unknown) => new Response(JSON.stringify(v), {
  status: 200, headers: { 'content-type': 'application/json' },
})
window.fetch = ((input: RequestInfo | URL) => {
  const url = String(typeof input === 'string' ? input : (input as Request).url ?? input)
  log.push('fetch ' + url)
  if (url.includes('/documents/')) {
    const id = url.split('/documents/')[1]!.split('?')[0]!
    return Promise.resolve(json(DOCS.find((d) => d.id === id) ?? DOCS[0]))
  }
  if (url.includes('/documents')) return Promise.resolve(json({ documents: DOCS }))
  return Promise.resolve(json({}))
}) as typeof fetch

function Fixture() {
  const [open, setOpen] = useState(true)
  const [second, setSecond] = useState(q.get('two') === '1')
  return <>
    <p>
      <button id="behind-modal" onClick={() => log.push('background clicked')}>
        Background control</button>
      <button id="reopen" onClick={() => setOpen(true)}>reopen the gallery</button>
      <button id="open-second" onClick={() => setSecond(true)}>open the second</button>
    </p>
    {open && <DocGalleryModal slug="probe" toast={(l) => log.push('toast ' + l)}
      close={() => { log.push('close gallery'); setOpen(false) }} />}
    {second && (
      <PinFrame kind="probe-second" title="second surface" panel="settings"
        close={() => { log.push('close second'); setSecond(false) }}>
        <h3 id="second-head">Second surface</h3>
        {/* long enough that the PANEL itself is the scroller: that is the
            case where a title bar which is not sticky scrolls away and takes
            the window's drag handle with it, and a shorter fixture would make
            that check free */}
        {Array.from({ length: 60 }, (_, i) => (
          <p key={i} className="second-para">Paragraph {i} of a panel that scrolls.</p>
        ))}
      </PinFrame>
    )}
  </>
}

createRoot(document.getElementById('root')!).render(<Fixture />)
