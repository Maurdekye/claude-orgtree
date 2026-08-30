// D-201: process warmth is a speed cue. The canvas must distinguish it without
// turning a normal cold start into an error or lifecycle state.

import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { NodeSquare } from '../src/canvas/cards'
import { ProcessWarmMark } from '../src/canvas/desk'
import type { CanvasNode } from '../src/canvas/shared'
import type { OpResult } from '../src/types'

const noop = () => {}
const op = () => Promise.resolve({} as OpResult)
const seats = { haiku: 1, sonnet: 2, opus: 5, fable: 10, luna: 1, terra: 2, sol: 5, flash: 1, pro: 2 }
const hire = { enabled: true, installed: true, reason: null }

function node(warm: boolean): CanvasNode {
  return { id: warm ? 'warm' : 'cold', state: 'live', tier: 'terra', model_id: 'terra',
    proc_warm: warm, children: [], seat: 2, grant: 0, free: 0,
    scope: { tools: {}, add_dirs: [] } }
}

function card(warm: boolean) {
  const n = node(warm)
  return mountView(<NodeSquare node={n} pos={{ x: 0, y: 0 }} lod="norm" focused={false}
    dragging={false} isDrop={false} seats={seats} codexHire={hire} geminiHire={hire} claudeHire={hire}
    map={new Map([[n.id, n]])} op={op} slug="org" toast={noop} pxc={1} zoom={1} compactAt={.8}
    pub={false} maxTop={0} kioskRemaining={null} cascadeAlloc onSpawn={noop} onSpawnSide={noop}
    onSpawnTop={noop} onConfig={noop} onInbox={noop} onLineage={noop} onOpenDoc={noop}
    onRecenter={noop} onJump={noop} onMailLink={noop} onDragStart={noop} onDragMove={noop}
    onDragEnd={noop} onDragCancel={noop} />, (el) => el)
}

test('live cards carry exactly one warm-cache class and a matching quiet marker', async (t) => {
  for (const warm of [false, true]) {
    const view = await card(warm)
    t.after(() => view.unmount())
    const root = view.el.querySelector('.sq')!
    assert.equal(root.classList.contains('proc-warm'), warm)
    assert.equal(root.classList.contains('proc-cold'), !warm)
    assert.equal(root.querySelector('.proc-mark')?.classList.contains(warm ? 'warm' : 'cold'), true)
  }
})

test('warm and cold marks use a visible shape distinction, never warning colour classes', async () => {
  const view = await mountView(<><ProcessWarmMark warm /><ProcessWarmMark warm={false} /></>, (el) => el)
  try {
    assert.equal(view.el.querySelectorAll('.proc-mark.warm').length, 1)
    assert.equal(view.el.querySelectorAll('.proc-mark.cold').length, 1)
    assert.equal(view.el.querySelectorAll('.proc-mark.bad, .proc-mark.err').length, 0)
  } finally { view.unmount() }
})
