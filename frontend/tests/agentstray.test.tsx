// agentstray.test.tsx — the AGENTS tray's bottom-left panel (user bug
// 2026-08-21): its max height was capped far short of the canvas, and
// hovering it to scroll zoomed the canvas instead.
//
// The mechanism: OrgCanvas attaches a NATIVE (non-passive) wheel listener to
// `.viewport` for pan/zoom, and always called `preventDefault()` unless the
// event target sat inside `.overlay` or `.desk-over`. `.tray` was not on that
// allowlist, so a wheel over the agents list was captured for zoom before the
// browser ever got a chance to scroll the (already overflow-y:auto) list.
//
// This only proves the fix at the DOM-event layer: jsdom does no layout, so
// it cannot show the list visually scrolling or the CSS max-height resolving
// against the canvas's real pixel height (see render.test.tsx's "jsdom does
// no layout" notes throughout). Two things this DOES prove: (1) a wheel event
// over the tray is no longer preventDefault()'d — which is what lets the
// browser's native overflow-y:auto scroll the list — and the canvas's own
// camera transform is provably untouched by it; (2) the CSS actually ships a
// canvas-bound (not viewport-fraction) max-height, read back from the real
// stylesheet the app ships.
//
// Run:  cd frontend && node tests/run.mjs agentstray

import { readFileSync } from 'node:fs'
import path from 'node:path'
import { flush, inAct, mountView, realClock, useFakeClock } from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import type { TreePayload } from '../src/types'

const noop = () => {}
const txt = (el: HTMLElement) => el.textContent ?? ''

function uiTest(name: string,
  body: (k: { mount: (el: React.ReactElement)
    => Promise<{ el: HTMLElement }> }) => Promise<void>): void {
  test(name, async (t: TestContext) => {
    useFakeClock()
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const m of open) { try { await m.unmount() } catch { /* gone */ } }
      realClock()
    })
    await body({
      mount: async (el) => {
        const v = await mountView(el, (host) => host)
        open.push(v)
        return { el: v.el }
      },
    })
  })
}

/** shaped like the payload, not type-checked into it — see mailwire.test.tsx,
 *  same fixture idiom, trimmed to what OrgCanvas actually dereferences */
const asTree = (v: unknown) => v as TreePayload

function tree(nodeIds: string[]): TreePayload {
  const mk = (id: string) => ({
    id, title: id, tier: 'haiku', model_id: 'haiku', state: 'live',
    seat: 1, grant: 0, free: 0, ui_order: 0, cost_usd: 0, occupancy: null,
    context_window: null, charter: null, mail_pending: 0, limit_locked: false,
    last_status: null, prev_status: null, inflight_at: null, last_denials: [],
    turns: [], frozen: null, audiences_held: [], bearer_state: null,
    generation: 0, children: [], lineage: [],
    scope: { permission_mode: 'default', add_dirs: [], tools: {}, org_visibility: 'team' },
  })
  return asTree({
    slug: 'mine', name: 'mine', workspace: null, dirs: [], max_top_grant: 1000,
    default_top_grant: 50, compact_at: 0, default_tools: null,
    default_visibility: 'team', default_effort: '', credit_requests: [],
    tiers: { haiku: 1, sonnet: 3, opus: 5, fable: 10 }, audiences: [],
    roots: nodeIds.map(mk), cost_usd_total: 0,
    audit: { live_nodes: nodeIds.length, top_level_holds: 0, no_overdraft: true, problems: [] },
    user_inbox_count: 0, user_inbox_newest: null, fable_lock: null,
    spend_frozen: false, storage_blocked: false, auto_resume: false,
    fable_limit_policy: 'freeze', fable_filter_policy: 'halt',
    cascade_hire: false, cascade_alloc: true, sandboxed: false,
    audience_requests: [], org_inbox: null, net: null,
  })
}

/** the canvas's camera transform — unaffected wheel events must leave this
 *  string byte-identical; a zoom rewrites both the scale() and translate() */
function spaceTransform(el: HTMLElement): string {
  const space = el.querySelector('.space') as HTMLElement | null
  assert.ok(space, 'the canvas world element (.space) did not render')
  return space!.style.transform
}

uiTest('§1 a wheel over the agents tray is not captured for canvas zoom',
  async ({ mount }) => {
    const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
    const { el } = await mount(
      <OrgCanvas tree={tree(['ceo', 'cto'])} op={() => Promise.resolve({} as never)}
        slug="mine" toast={noop} mailEvt={null} />)
    await flush()
    const toggle = [...el.querySelectorAll('.tray-toggle')][0] as HTMLElement
    assert.ok(toggle, 'the agents toggle button rendered')
    assert.ok(txt(toggle).toLowerCase().includes('agents'), 'and it is labeled "agents"')
    await inAct(() => { toggle.click() })
    await flush()
    const tray = el.querySelector('.tray') as HTMLElement | null
    assert.ok(tray, 'the tray opened')
    const before = spaceTransform(el)

    // a real wheel gesture, over a row inside the tray — cancelable and
    // bubbling, exactly as the browser dispatches it
    const row = tray!.querySelector('.tray-row') as HTMLElement | null
    assert.ok(row, 'the fixture agents rendered as tray rows')
    const evt = new WheelEvent('wheel', {
      deltaY: 120, bubbles: true, cancelable: true,
    })
    row!.dispatchEvent(evt)

    assert.equal(evt.defaultPrevented, false,
      'the canvas wheel handler preventDefault()d a wheel over the tray — '
      + 'the browser can no longer run its own overflow-y:auto scroll there')
    assert.equal(spaceTransform(el), before,
      'the canvas camera moved in response to a wheel over the tray — it '
      + 'zoomed instead of the tray scrolling')
  })

uiTest('§2 a wheel over the empty canvas still zooms (the carve-out is scoped)',
  async ({ mount }) => {
    // the fix must not go the OTHER way either — the wheel handler still
    // owns pan/zoom everywhere outside the tray/overlay/desk carve-outs
    const { OrgCanvas } = await import('../src/canvas/OrgCanvas')
    const { el } = await mount(
      <OrgCanvas tree={tree(['ceo'])} op={() => Promise.resolve({} as never)}
        slug="mine" toast={noop} mailEvt={null} />)
    await flush()
    const viewport = el.querySelector('.viewport') as HTMLElement
    assert.ok(viewport, 'the canvas viewport rendered')
    const before = spaceTransform(el)
    const evt = new WheelEvent('wheel', {
      deltaY: -120, bubbles: true, cancelable: true,
      clientX: 50, clientY: 50,
    })
    await inAct(() => { viewport.dispatchEvent(evt) })
    await flush()
    assert.equal(evt.defaultPrevented, true,
      'a wheel on the bare canvas must still be captured for zoom')
    assert.notEqual(spaceTransform(el), before,
      'and the camera must actually have zoomed')
  })

// ------------------------------------------------------------------- height
// jsdom does no layout (see the file banner), so the CSS is read back from
// the real stylesheet the app ships, not from a computed box.

declare const __SRC_DIR__: string
const CSS = readFileSync(path.join(__SRC_DIR__, 'styles.css'), 'utf8')

function rule(selector: string): string {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const re = new RegExp(esc + String.raw`\s*\{([^}]*)\}`)
  const m = re.exec(CSS)
  assert.ok(m, `no "${selector}" rule found in styles.css`)
  return m![1]!
}

test('§3 the tray’s max-height is bound to the canvas, not a small fixed slice', () => {
  const trayCss = rule('.tray')
  assert.match(trayCss, /overflow-y:\s*auto/,
    'the tray must stay a real scroll container')
  const mh = /max-height:\s*([^;]+);/.exec(trayCss)
  assert.ok(mh, 'the tray declares a max-height at all')
  const value = mh![1]!.trim()
  assert.doesNotMatch(value, /^\d+vh$/,
    `"${value}" is a fixed viewport-height slice, not the canvas's own bound `
    + '— it under- or over-shoots whenever the header/canvas ratio changes')
  // the wrap must give .tray a definite containing-block height for a
  // percentage max-height to mean anything (a flex item's % height resolves
  // to nothing against an auto-height container) — asserted structurally so
  // a revert that keeps "100%" but drops `top` silently breaks it again
  const wrapCss = rule('.tray-wrap')
  assert.match(wrapCss, /top:\s*[\d.]+px/,
    '.tray-wrap needs both top and bottom set — bottom alone leaves its '
    + 'height auto, and .tray’s max-height: 100% would resolve to nothing')
  assert.match(wrapCss, /bottom:\s*[\d.]+px/)
})
