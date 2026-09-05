// refmd.test.tsx — canonical references inside rendered markdown.
//
// The unit under test walks the DOM the sanitizer produced, so every check
// here starts from REAL `md()` output rather than from hand-written HTML: the
// thing that breaks this pass is what marked and DOMPurify actually emit
// (a token inside `<code>`, a token inside an anchor's text, a body split
// across paragraphs), not what I would have written by hand.
//
// Run:  cd frontend && node tests/run.mjs refmd

import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { linkifyRefs, refClickHandler, unlinkifyRefs, useRefMd } from '../src/canvas/refmd'
import { RefChip } from '../src/canvas/reflinks'
import { resolveRef } from '../src/canvas/reflinks'
import type { RefWorld } from '../src/canvas/reflinks'
import { parseRef } from '../src/canvas/workrefs'
import { md } from '../src/canvas/shared'

const HERE = 'orgtree'

const world = (o: Partial<RefWorld> = {}): RefWorld =>
  ({ org: HERE, ...o })

/** a container holding what the app would actually render for `text` */
function body(text: string): HTMLElement {
  const el = document.createElement('div')
  el.className = 'mailer-body md'
  el.innerHTML = md(text).__html
  return el
}

const chips = (el: HTMLElement) =>
  [...el.querySelectorAll('[data-ref-token]')] as HTMLElement[]

/** what the sentence READS as: the chips' own trailing status word is
 *  chrome, and `md()` leaves a trailing newline outside the paragraph */
const readable = (el: HTMLElement): string => {
  const c = el.cloneNode(true) as HTMLElement
  c.querySelectorAll('.ref-why').forEach((w) => w.remove())
  return (c.textContent ?? '').trim()
}

test('§1 a token in prose becomes a chip and the sentence survives', () => {
  const el = body('blocked behind @item:orgtree/alpha until Friday')
  const n = linkifyRefs(el, world({ items: new Map([['alpha', 'alpha']]) }))
  assert.equal(n, 1)
  const [c] = chips(el)
  assert.equal(c!.tagName, 'BUTTON', 'a resolvable reference is a control')
  assert.equal(c!.textContent, 'alpha')
  // ⚠ EVERY OTHER WORD IS STILL THERE, IN ORDER. A walk that drops or
  // reorders the text around its match has broken what it was decorating.
  assert.equal(readable(el), 'blocked behind alpha until Friday')
})

test('§2 CONTROL — a token this org does not have is marked, not deleted and '
  + 'not silently left as prose', () => {
    const el = body('see @item:orgtree/ghost for the rest')
    linkifyRefs(el, world({ items: new Map() }))
    const [c] = chips(el)
    assert.ok(c, 'the dead reference still rendered')
    assert.equal(c!.tagName, 'SPAN', 'and it is not a control')
    assert.ok(c!.className.includes('ref-absent'))
    // it shows what was WRITTEN — the token, not the bare id
    assert.match(c!.textContent!, /@item:orgtree\/ghost/)
  })

test('§3 A TOKEN INSIDE CODE IS LEFT ALONE — it is being quoted', () => {
  // both fences: `inline` and a block. The author is DISCUSSING the token.
  const el = body('write `@item:orgtree/alpha` like this\n\n'
    + '```\n@item:orgtree/alpha\n```\n\nand then @item:orgtree/alpha works')
  const w = world({ items: new Map([['alpha', 'alpha']]) })
  const n = linkifyRefs(el, w)
  // POSITIVE CONTROL FIRST: the one in prose DID become a chip, so "nothing
  // was touched" cannot be why the code survived.
  assert.equal(n, 1, 'exactly the prose occurrence was linked')
  assert.equal(el.querySelectorAll('code [data-ref-token]').length, 0)
  assert.match(el.querySelector('code')!.textContent!, /@item:orgtree\/alpha/)
  assert.match(el.querySelector('pre')!.textContent!, /@item:orgtree\/alpha/)
})

test('§4 CONTROL — a token in an anchor\'s text stays part of the link', () => {
  const el = body('see [@item:orgtree/alpha](https://x.dev/a) please, '
    + 'and @item:orgtree/alpha')
  const n = linkifyRefs(el, world({ items: new Map([['alpha', 'alpha']]) }))
  assert.equal(n, 1, 'only the loose one was linked')
  assert.equal(el.querySelectorAll('a [data-ref-token]').length, 0)
  assert.equal(el.querySelector('a')!.getAttribute('href'), 'https://x.dev/a',
    'and the anchor is untouched')
})

test('§5 THE SECOND PASS DOES NOT WALK ITS OWN OUTPUT', () => {
  const el = body('@item:orgtree/alpha and @item:orgtree/beta')
  const w1 = world({ items: 'loading' as const })
  linkifyRefs(el, w1)
  assert.equal(chips(el).length, 2)
  assert.ok(chips(el).every((c) => c.className.includes('ref-pending')))
  // the index arrives: same html, a different answer
  const w2 = world({ items: new Map([['alpha', 'alpha']]) })
  linkifyRefs(el, w2)
  const after = chips(el)
  assert.equal(after.length, 2, 'still two chips — not four, not nested')
  assert.ok(after[0]!.className.includes('ref-ready'))
  assert.ok(after[1]!.className.includes('ref-absent'))
  assert.equal(el.querySelectorAll('[data-ref-token] [data-ref-token]').length,
    0, 'no chip was rebuilt inside another')
  assert.equal(readable(el), 'alpha and @item:orgtree/beta')
})

test('§6 A PASS THAT CHANGES NOTHING TOUCHES NOTHING — the reader\'s selection '
  + 'survives an unrelated poll', () => {
    const el = body('see @item:orgtree/alpha now')
    const w = world({ items: new Map([['alpha', 'alpha']]) })
    linkifyRefs(el, w)
    const first = chips(el)[0]
    // a DIFFERENT world object saying the same thing — this is what a poll
    // produces: a new Map, identical contents
    const n = linkifyRefs(el, world({ items: new Map([['alpha', 'alpha']]) }))
    assert.equal(n, -1, 'the pass reported that it did nothing')
    assert.equal(chips(el)[0], first,
      'the very same element node is still on screen')
  })

test('§7 unlinkify restores exactly what the author wrote', () => {
  const src = 'see @item:orgtree/alpha and @mail:orgtree/user/m1 now'
  const el = body(src)
  // ⚠ THE MARKUP, NOT THE TEXT. "exactly what the author wrote" is a claim
  // about the html the sanitizer produced, and comparing textContent would
  // let an undo that dropped an element or an attribute pass.
  const before = el.innerHTML
  linkifyRefs(el, world({ items: new Map([['alpha', 'alpha']]) }))
  assert.notEqual(el.innerHTML, before, 'the pass did change the markup')
  const n = unlinkifyRefs(el)
  assert.equal(n, 2)
  assert.equal(el.innerHTML, before, 'and undoing it is exact')
  // ⚠ AND THE TEXT NODES ARE WHOLE AGAIN. Left split, the next pass sees
  // neighbouring fragments and matches nothing across the seam.
  const p = el.querySelector('p')!
  assert.equal(p.childNodes.length, 1, 'the paragraph is one text node again')
  assert.equal((p.firstChild as Text).data, src)
})

test('§8 the html is never re-parsed — a chip label cannot inject markup', () => {
  // the label comes from the index, which is server data. If this function
  // ever built HTML strings, this is the entry.
  const el = body('see @item:orgtree/alpha now')
  linkifyRefs(el, world({ items: new Map([['alpha', '<img src=x onerror=1>']]) }))
  const c = chips(el)[0]!
  assert.equal(c.textContent, '<img src=x onerror=1>')
  assert.equal(c.querySelector('img'), null, 'it is text, not markup')
})

test('§9 CONTROL — the DOM chip and the React chip agree, outcome for outcome',
  async () => {
    // ⚠ TWO RENDERERS OF ONE DECISION IS THE DRIFT THIS PINS. The markdown
    // pass cannot use RefChip (there are no React children in innerHTML), so
    // it has a second implementation, and a second implementation that
    // quietly disagrees is worse than either alone.
    const cases: [string, RefWorld][] = [
      ['@item:orgtree/alpha', world({ items: new Map([['alpha', 'alpha']]) })],
      ['@item:orgtree/ghost', world({ items: new Map() })],
      ['@item:orgtree/alpha', world({ items: 'loading' })],
      ['@item:elsewhere/alpha', world({})],
      ['@doc:orgtree/d1', world({ handles: new Set(['item']) })],
    ]
    for (const [token, w] of cases) {
      const el = body(`x ${token} y`)
      linkifyRefs(el, w)
      const dom = chips(el)[0]!
      const r = resolveRef(parseRef(token)!, w)
      const view = await mountView(<RefChip r={r} onOpen={() => {}} />,
        (host) => host)
      try {
        const react = view.el.querySelector('.ref-chip') as HTMLElement
        assert.equal(dom.className, react.className, `class for ${token}`)
        assert.equal(dom.textContent, react.textContent, `text for ${token}`)
        assert.equal(dom.title, react.title, `title for ${token}`)
        assert.equal(dom.tagName, react.tagName, `element for ${token}`)
      } finally { await view.unmount() }
    }
  })

test('§10 with no handler the chips are inert — no control that swallows a '
  + 'click and does nothing', () => {
    const el = body('see @item:orgtree/alpha now')
    linkifyRefs(el, world({ items: new Map([['alpha', 'alpha']]) }), false)
    assert.equal(chips(el)[0]!.tagName, 'SPAN')
  })

test('§11 the click is decided AGAIN, against the world as it is now', () => {
  const el = body('see @item:orgtree/alpha now')
  let w = world({ items: new Map([['alpha', 'alpha']]) })
  const opened: string[] = []
  linkifyRefs(el, w)
  el.addEventListener('click', refClickHandler(() => w,
    (r) => opened.push(r.ref.id)))
  chips(el)[0]!.click()
  assert.deepEqual(opened, ['alpha'])
  // the item goes away while the rendered chip still says otherwise — a
  // stale picture must not open anything
  w = world({ items: new Map() })
  chips(el)[0]!.click()
  assert.deepEqual(opened, ['alpha'], 'the stale chip opened nothing')
})

// --------------------------------------------------------------- in a component

function uiTest(name: string,
  body2: (mount: (el: React.ReactElement) => Promise<HTMLElement>)
    => Promise<void>): void {
  test(name, async (t: TestContext) => {
    useFakeClock()
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const m of open) { try { await m.unmount() } catch { /* gone */ } }
      realClock()
    })
    await body2(async (el) => {
      const v = await mountView(el, (host) => host)
      open.push(v)
      return v.el
    })
  })
}

function Pane({ text, w, onOpen }: {
  text: string
  w: RefWorld
  onOpen?: (id: string) => void
}) {
  const host = useRefMd(w, onOpen ? (r) => onOpen(r.ref.id) : undefined)
  return <div ref={host} className="mailer-body md"
    dangerouslySetInnerHTML={md(text)} />
}

uiTest('§12 a component rendering markdown gets its references linked, and '
  + 'clicking one reaches the handler', async (mount) => {
    const opened: string[] = []
    const el = await mount(
      <Pane text="blocked behind @item:orgtree/alpha today"
        w={world({ items: new Map([['alpha', 'alpha']]) })}
        onOpen={(id) => opened.push(id)} />)
    await flush()
    const c = el.querySelector('[data-ref-token]') as HTMLElement
    assert.ok(c, 'the mounted body was walked')
    await inAct(() => c.click())
    await flush()
    assert.deepEqual(opened, ['alpha'])
  })

uiTest('§13 the body changing REPLACES the chips rather than losing them',
  async (mount) => {
    // React swaps the innerHTML wholesale, which takes the chips with it —
    // the pass has to notice and run again on the new html.
    const w = world({ items: new Map([['alpha', 'alpha'], ['beta', 'beta']]) })
    const view = await mountView(
      <Pane text="first @item:orgtree/alpha" w={w} />, (host) => host)
    try {
      await flush()
      assert.equal(view.el.querySelector('[data-ref-token]')!.textContent,
        'alpha')
      await view.render(<Pane text="second @item:orgtree/beta" w={w} />)
      await flush()
      const after = [...view.el.querySelectorAll('[data-ref-token]')]
      assert.equal(after.length, 1, 'exactly one chip in the new body')
      assert.equal(after[0]!.textContent, 'beta')
    } finally { await view.unmount() }
  })
