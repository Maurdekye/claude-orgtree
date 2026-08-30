// Turn-start mail is one transcript event but can carry several authors.
// Keep its envelope structured so passive notices never become user markdown.

import test from 'node:test'
import assert from 'node:assert/strict'
import './harness'
import { splitTurnMail } from '../src/canvas/desk'

const SAMPLE = `[MAIL — 2 message(s)]
NOTICE FROM process-cache-2 (your report) · 2026-08-30T12:11:32.854Z — informational, delivered passively; no reply is expected
Measured **only this phrase** should be bold.
---
FROM @user (USER) · message · 2026-08-30T12:12:00.000Z
Can agents distinguish mail from a direct message?
[END MAIL]`

test('turn-start envelope preserves each sender as an independent card payload', () => {
  const got = splitTurnMail(SAMPLE)
  assert.equal(got.rest, '')
  assert.deepEqual(got.mail.map((m) => ({ from: m.from, kind: m.kind, passive: m.passive })), [
    { from: 'process-cache-2', kind: 'notice', passive: true },
    { from: '@user', kind: 'message', passive: false },
  ])
  assert.equal(got.mail[0]!.relationship, 'your report')
  assert.equal(got.mail[0]!.at, '2026-08-30T12:11:32.854Z')
  assert.equal(got.mail[0]!.body, 'Measured **only this phrase** should be bold.')
  assert.equal(got.mail[1]!.body, 'Can agents distinguish mail from a direct message?')
})

test('a malformed future envelope remains visible as ordinary transcript text', () => {
  const raw = '[MAIL — 1 message(s)]\nNEW SHAPE\n[END MAIL]'
  assert.deepEqual(splitTurnMail(raw), { mail: [], rest: raw })
})
