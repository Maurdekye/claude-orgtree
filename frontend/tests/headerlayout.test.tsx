import './harness'
import { FakeServer, flush, installFetch, mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { DeskChat } from '../src/canvas/desk'
import type { CanvasNode } from '../src/canvas/shared'
import type { OpResult } from '../src/types'

declare const __SRC_DIR__: string

const noop = () => {}
const op = () => Promise.resolve({} as OpResult)

test('desk header has bounded controls and a separate wrapping metadata row', async (t) => {
  installFetch(new FakeServer())
  const id = 'an-agent-name-long-enough-to-wrap-at-high-zoom'
  const n: CanvasNode = {
    id, state: 'live', tier: 'haiku', model_id: 'haiku', children: [],
    seat: 1, grant: 4, free: 1, cost_usd: 12.34,
    occupancy: 85000, context_window: 100000,
    scope: { tools: {}, add_dirs: [] },
    audiences_held: ['@user', '@extern', 'very-long-audience-name-that-must-wrap'],
    last_status: { status: 'working', summary: 'still working', at: '2026-09-01T10:00:00Z' },
    busy: true, waiting: false, responding: true, inflight_at: '2026-09-01T10:00:00Z',
    proc_live: true, proc_warm: false, proc_relaunch: true,
    proc_relaunch_reason: 'identity-changed — system prompt changed',
    mcp_tool_count: 12, last_turn_mcp_tool_count: 9,
    mcp_tool_count_provider: 'claude', mcp_tool_count_source: 'system/init.tools',
    cache_forecast: {
      generation: 'g', state: 'known_incompatible', reason: 'tools changed',
      source: 'identity', lane: 'subscription', last_receipt_at: '2026-09-01T10:00:00Z',
      ttl_seconds: 3600, expires_at: '2026-09-01T11:00:00Z',
      changed_inputs: ['callable tools'], precompact_action: 'miss_expected',
      precompact_reason: 'below threshold',
    },
    queued: 7, ran_as_label: 'fallback 2 · safe-label',
  }
  const view = await mountView(
    <DeskChat node={n} map={new Map([[id, n]])} op={op} slug="header"
      toast={noop} pub={false} bare />,
    (el) => el,
  )
  t.after(() => view.unmount())
  await flush()
  const head = view.el.querySelector('.cc-head')!
  const top = head.querySelector(':scope > .cc-head-top')!
  const meta = head.querySelector(':scope > .cc-head-meta')!
  assert.ok(top && meta)
  for (const sel of ['.tier', '.cc-name', '.cc-actions', '.cc-tabs', '.cc-icon']) {
    assert.ok(top.querySelector(sel), `top row omitted ${sel}`)
  }
  assert.equal(top.querySelectorAll('.cc-tabs button').length, 4)
  for (const sel of [
    '.ctxwheel', '.proc-state', '.mcp-tool-count', '.cache-forecast',
    '.statuschip', '.cc-working', '.badge',
  ]) assert.ok(meta.querySelector(sel), `metadata row omitted ${sel}`)
  assert.equal(head.querySelectorAll('.proc-state').length, 1)
  assert.equal(head.querySelectorAll('.proc-state .proc-mark').length, 0,
    'the desk header still renders separate live and warm lights')
  assert.equal(top.querySelector('.mcp-tool-count'), null)
  assert.equal(top.querySelector('.cache-forecast'), null)
  assert.ok(view.el.querySelector('.cache-send-warning.miss'),
    'known incompatibility warning is not attached above the composer')
})

test('layout CSS wraps naturally and pins finite controls above metadata', () => {
  const css = readFileSync(path.join(__SRC_DIR__, 'styles.css'), 'utf8')
  assert.match(css, /\.cc-head\s*\{[^}]*flex-direction:\s*column/s)
  assert.match(css, /\.cc-head-top\s*\{[^}]*flex-wrap:\s*wrap/s)
  assert.match(css, /\.cc-head-meta\s*\{[^}]*flex-wrap:\s*wrap/s)
  assert.match(css, /\.cc-tabs button\s*\{[^}]*min-height:\s*24px/s)
  assert.match(css, /\.cc-actions button\s*\{[^}]*min-height:\s*24px/s)
  assert.match(css, /\.cc-name\s*\{[^}]*overflow-wrap:\s*anywhere/s)
  assert.doesNotMatch(css, /\.eye-chat \.cc-name\s*\{[^}]*text-overflow:\s*ellipsis/s)
})
