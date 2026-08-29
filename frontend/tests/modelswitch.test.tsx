// The settings model switch is provider-aware. It renders the ledger's two
// tier families, their real seat prices, and the same availability promises
// the backend's provider_hire_gate enforces.

import {
  FakeServer, installFetch, mountView, realClock, useFakeClock,
} from './harness'
import test from 'node:test'
import type { TestContext } from 'node:test'
import assert from 'node:assert/strict'
import { ActiveAgentSummary } from '../src/App'
import { NodeConfig } from '../src/canvas/modals'
import { USER } from '../src/canvas/shared'
import type { CanvasNode } from '../src/canvas/shared'
import type { OpRequest, OpResult, ProviderInfo, TreeNode, TreePayload } from '../src/types'

const noop = () => {}

function node(tier = 'haiku'): CanvasNode {
  return {
    id: 'agent', title: 'agent', state: 'live', tier, model_id: tier,
    parent: USER, children: [], seat: 1, grant: 10, free: 10,
    scope: { permission_mode: 'acceptEdits', add_dirs: [], tools: {
      bash: true, web: true, edit: true, subagents: true, mcp: [],
    }, org_visibility: 'team' },
    charter: '', team_charter: '', turns: [], audiences_held: [],
  }
}

function tree(extra: Partial<TreePayload> = {}): TreePayload {
  return {
    slug: 'org', dirs: [], tiers: {
      haiku: 1, sonnet: 2, opus: 5, fable: 10,
      luna: 1, terra: 2, sol: 5, flash: 1, pro: 2,
    }, max_top_grant: 100, default_effort: '', effort_default: 'high',
    cascade_hire: true, sandboxed: false, ...extra,
  } as TreePayload
}

function provider(extra: Partial<ProviderInfo> = {}): ProviderInfo {
  return {
    id: 'openai', label: 'Codex', cli: 'Codex CLI', tiers: [],
    status: { installed: true, connected: true, kind: 'chatgpt' },
    hire_enabled: true, reason: null, ...extra,
  }
}

type Mounted = { el: HTMLElement; ops: OpRequest[] }

function configTest(name: string, body: (mount: (o?: {
  node?: CanvasNode; tree?: TreePayload; provider?: ProviderInfo | null
  gemini?: ProviderInfo | null
}) => Promise<Mounted>) => Promise<void> | void): void {
  test(name, async (t: TestContext) => {
    useFakeClock(); installFetch(new FakeServer())
    const open: { unmount: () => Promise<void> }[] = []
    t.after(async () => {
      for (const v of open) await v.unmount()
      realClock()
    })
    await body(async (o = {}) => {
      const ops: OpRequest[] = []
      const nd = o.node ?? node()
      const v = await mountView(
        <NodeConfig node={nd} map={new Map([[nd.id, nd]])}
          tree={o.tree ?? tree()} slug="org"
          op={(x) => { ops.push(x); return Promise.resolve({} as OpResult) }}
          toast={noop} codexProvider={o.provider === undefined ? provider() : o.provider}
          geminiProvider={o.gemini === undefined
            ? provider({ id: 'google', label: 'Gemini', cli: 'Gemini CLI',
                         status: { installed: true, connected: true,
                                   kind: 'api-key' } })
            : o.gemini}
          close={noop} />,
        (el) => el,
      )
      open.push(v)
      return { el: v.el, ops }
    })
  })
}

const options = (el: HTMLElement) =>
  [...el.querySelectorAll<HTMLOptionElement>('.model-switch option')]
const option = (el: HTMLElement, tier: string) =>
  options(el).find((o) => o.value === tier)!

test('the header summary counts both provider families', async (t: TestContext) => {
  useFakeClock()
  const tiers = ['opus', 'luna', 'terra', 'sol']
  const roots = tiers.map((tier, i) => ({
    ...node(tier), id: tier, busy: i === tiers.length - 1,
  })) as unknown as TreeNode[]
  const view = await mountView(
    <ActiveAgentSummary tree={tree({ roots })} />,
    (el) => el,
  )
  t.after(async () => { await view.unmount(); realClock() })
  assert.match(view.el.textContent ?? '', /4 live · 1 working/)
  assert.deepEqual(
    [...view.el.querySelectorAll<HTMLBRElement>('.agents b')]
      .map((b) => [b.className, b.textContent]),
    [['t-opus', 'O1'], ['t-luna', 'L1'], ['t-terra', 'T1'], ['t-sol', 'S1']],
  )
})

configTest('the switch lists every provider family with its ledger seats',
  async (mount) => {
    const { el } = await mount()
    const groups = [...el.querySelectorAll('select.model-switch optgroup')]
    // grew to three at D-189 (gemini)
    assert.deepEqual(groups.map((g) => g.getAttribute('label')),
      ['Claude', 'Codex', 'Gemini'])
    assert.deepEqual(options(el).map((o) => [o.value, o.textContent?.trim()]), [
      ['haiku', 'haiku · seat 1'], ['sonnet', 'sonnet · seat 2'],
      ['opus', 'opus · seat 5'], ['fable', 'fable · seat 10'],
      ['luna', 'luna · seat 1'], ['terra', 'terra · seat 2'],
      ['sol', 'sol · seat 5'],
      ['flash', 'flash · seat 1'], ['pro', 'pro · seat 2'],
    ])
  })

// D-196 (user ruling 2026-08-29): a provider-crossing switch now asks first,
// so `save` alone no longer sends the op — the CONFIRM does. This test kept
// its original claim (the crossing reaches the backend intact) and gained the
// step that now stands in front of it; the gate itself is covered in full by
// tests/crossprovider.test.tsx, including that cancelling sends nothing.
configTest('a confirmed provider-crossing switch sends the switch_model op',
  async (mount) => {
    const { el, ops } = await mount()
    const { act } = await import('react')
    const select = el.querySelector<HTMLSelectElement>('.model-switch')!
    await act(async () => {
      select.value = 'sol'
      select.dispatchEvent(new Event('change', { bubbles: true }))
    })
    const save = [...el.querySelectorAll<HTMLButtonElement>('button')]
      .find((b) => b.textContent?.trim() === 'save')!
    await act(async () => { save.click() })
    assert.equal(ops.find((o) => o.op === 'switch_model'), undefined,
      'the switch must not fire before the user confirms')
    const confirm = [...document.querySelectorAll<HTMLButtonElement>(
      '.confirm-box button')].find((b) => /^switch to sol/.test(
      b.textContent?.trim() ?? ''))!
    await act(async () => { confirm.click() })
    assert.deepEqual(ops.find((o) => o.op === 'switch_model'), {
      op: 'switch_model', node: 'agent', tier: 'sol',
    })
  })

configTest('disconnected Codex tiers stay visible and explain why disabled',
  async (mount) => {
    const { el } = await mount({ provider: provider({
      hire_enabled: false,
      reason: 'not signed in — run `codex login` on this machine',
      status: { installed: true, connected: false, kind: null },
    }) })
    for (const tier of ['luna', 'terra', 'sol']) {
      assert.equal(option(el, tier).disabled, true)
      assert.match(option(el, tier).textContent ?? '', /not signed in/)
    }
    assert.equal(option(el, 'haiku').disabled, false)
  })

configTest('kiosk policy and seat cap disable options instead of hiding them',
  async (mount) => {
    const { el } = await mount({ tree: tree({
      kiosk: { max_tier: 'sonnet' } as TreePayload['kiosk'],
    }) })
    assert.equal(options(el).length, 9)   // 4 claude + 3 codex + 2 gemini
    for (const tier of ['luna', 'terra', 'sol', 'flash', 'pro']) {
      assert.equal(option(el, tier).disabled, true)
      assert.match(option(el, tier).textContent ?? '', /unavailable in kiosk orgs/)
    }
    assert.match(option(el, 'opus').textContent ?? '', /above kiosk cap \(sonnet\)/)
    assert.match(option(el, 'fable').textContent ?? '', /above kiosk cap \(sonnet\)/)
    assert.equal(option(el, 'haiku').disabled, false)
    assert.equal(option(el, 'sonnet').disabled, false)
  })

configTest('headless Codex requires an API-key login', async (mount) => {
  const blocked = await mount({ tree: tree({ headless: true }) })
  assert.equal(option(blocked.el, 'sol').disabled, true)
  assert.match(option(blocked.el, 'sol').textContent ?? '', /API-key login/)

  const allowed = await mount({
    tree: tree({ headless: true }),
    provider: provider({ status: {
      installed: true, connected: true, kind: 'api-key',
    } }),
  })
  assert.equal(option(allowed.el, 'sol').disabled, false)
})

configTest('a grandfathered current tier remains a truthful no-op',
  async (mount) => {
    const { el } = await mount({
      node: node('sol'),
      provider: provider({ hire_enabled: false, reason: 'not signed in',
        status: { installed: true, connected: false, kind: null } }),
    })
    assert.equal(option(el, 'sol').disabled, false)
    assert.equal(option(el, 'terra').disabled, true)
  })
