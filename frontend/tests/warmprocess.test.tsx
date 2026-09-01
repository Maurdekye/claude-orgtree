// D-201: process warmth is a speed cue. The canvas must distinguish it without
// turning a normal cold start into an error or lifecycle state.

import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { NodeSquare } from '../src/canvas/cards'
import { DestinationBusy, ProcessLifecycleMark } from '../src/canvas/desk'
import type { CanvasNode } from '../src/canvas/shared'
import type { OpResult } from '../src/types'

const noop = () => {}
const op = () => Promise.resolve({} as OpResult)
const seats = { haiku: 1, sonnet: 2, opus: 5, fable: 10, luna: 1, terra: 2, sol: 5, flash: 1, pro: 2 }
const hire = { enabled: true, installed: true, reason: null }

function node(warm: boolean): CanvasNode {
  return { id: warm ? 'warm' : 'cold', state: 'live', tier: 'terra', model_id: 'terra',
    proc_warm: warm, proc_live: warm, proc_relaunch: false,
    proc_relaunch_reason: null, children: [], seat: 2, grant: 0, free: 0,
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

test('live cards retain warm-cache styling while using the unified cue', async (t) => {
  for (const warm of [false, true]) {
    const view = await card(warm)
    t.after(() => view.unmount())
    const root = view.el.querySelector('.sq')!
    assert.equal(root.classList.contains('proc-warm'), warm)
    assert.equal(root.classList.contains('proc-cold'), !warm)
    assert.equal(root.querySelectorAll('.proc-state').length, 1)
    assert.equal(root.querySelector('.proc-state')?.classList.contains(warm ? 'ready' : 'off'), true)
    assert.equal(root.querySelectorAll('.proc-mark').length, 0)
  }
})

test('the desk header collapses live and warm into one accessible process cue', async () => {
  const view = await mountView(<>
    <ProcessLifecycleMark warm live />
    <ProcessLifecycleMark warm={false} live={false} />
    <ProcessLifecycleMark warm={false} live busy />
    <ProcessLifecycleMark warm={false} live />
    <ProcessLifecycleMark warm={false} live relaunch
      reason="identity-changed — system prompt changed" />
  </>, (el) => el)
  try {
    assert.equal(view.el.querySelectorAll('.proc-state').length, 5)
    assert.equal(view.el.querySelectorAll('.proc-one-mark').length, 5)
    assert.equal(view.el.querySelectorAll('.proc-state.ready').length, 1)
    assert.equal(view.el.querySelectorAll('.proc-state.off').length, 1)
    assert.equal(view.el.querySelectorAll('.proc-state.live').length, 2)
    assert.equal(view.el.querySelectorAll('.proc-state.relaunch').length, 1)
    assert.equal(view.el.querySelectorAll('.proc-state .proc-mark').length, 0,
      'the header regressed to a second persistent warm/cold dot')
    const labels = [...view.el.querySelectorAll<HTMLElement>('.proc-state')]
      .map((el) => el.getAttribute('aria-label') ?? '')
    assert.ok(labels.some((v) => /parked and ready/.test(v)))
    assert.ok(labels.some((v) => /claimed by the current turn/.test(v)))
    assert.ok(labels.some((v) => /spawning or initializing/.test(v)))
    assert.ok(labels.some((v) => /no CLI process live/.test(v)))
    const relaunch = view.el.querySelector<HTMLElement>('.proc-relaunch')
      ?.closest<HTMLElement>('.proc-state')
    assert.match(relaunch?.title ?? '', /identity-changed — system prompt changed/)
  } finally { view.unmount() }
})

test('idle process cue is a native toggle with an honest disabled reason', async () => {
  let toggles = 0
  const view = await mountView(<>
    <ProcessLifecycleMark warm live controlEnabled controlAction="stop"
      onToggle={() => { toggles += 1 }} />
    <ProcessLifecycleMark warm={false} live={false} paused controlEnabled
      controlAction="start" onToggle={() => { toggles += 1 }} />
    <ProcessLifecycleMark warm live controlAction="stop" controlEnabled={false}
      controlReason="the agent is responding" onToggle={() => { toggles += 1 }} />
  </>, (el) => el)
  try {
    const buttons = [...view.el.querySelectorAll<HTMLButtonElement>(
      'button.proc-toggle')]
    assert.equal(buttons.length, 3)
    assert.equal(buttons[0]!.getAttribute('aria-pressed'), 'false')
    assert.equal(buttons[1]!.getAttribute('aria-pressed'), 'true')
    assert.equal(buttons[2]!.disabled, true)
    assert.match(buttons[0]!.title, /click to stop/)
    assert.match(buttons[1]!.title, /click to start/)
    assert.match(buttons[2]!.title, /agent is responding/)
    buttons[0]!.click()
    buttons[2]!.click()
    assert.equal(toggles, 1, 'disabled controls must not invoke their handler')
  } finally { view.unmount() }
})

test('navigation busy arrows carry the destination provider, not an ancestor theme', async () => {
  const view = await mountView(<div className="prov-openai">
    <DestinationBusy tier="haiku" />
    <DestinationBusy tier="terra" />
    <DestinationBusy tier="pro" />
  </div>, (el) => el)
  try {
    const spins = [...view.el.querySelectorAll('.cc-spin')]
    assert.equal(spins[0]!.classList.contains('prov-claude'), true)
    assert.equal(spins[1]!.classList.contains('prov-openai'), true)
    assert.equal(spins[2]!.classList.contains('prov-google'), true)
  } finally { view.unmount() }
})
