import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import path from 'node:path'
import { isEvent, isPublicEvent } from '../src/events/decode'
import { projectEvent } from '../src/events/project'
import { FAMILIES, MANIFEST } from '../src/generated/events'
declare const __SRC_DIR__: string
const directory = path.resolve(__SRC_DIR__, '../tests/fixtures/events')
const fixtures = readdirSync(directory).filter(f => f.endsWith('.json')).map(f => JSON.parse(readFileSync(path.join(directory, f), 'utf8')))
test('every canonical leaf places all and only its human fields in both profiles', () => {
  const covered = new Set<string>()
  for (const f of fixtures) {
    assert.ok(isEvent(f.private)); assert.ok(isPublicEvent(f.public))
    for (const event of [f.private, f.public]) {
      const view = projectEvent(event); covered.add(view.family)
      assert.equal(view.family, f.family)
      assert.ok(view.title.length > 0)
      const expected = Object.entries(MANIFEST.leaves[event.variant].fields)
        .filter(([key, field]) => !['v','variant','actor','object','engine_authored'].includes(key)
          && ['both','human_only'].includes(field.disposition) && Object.hasOwn(event,key)).map(([key]) => key).sort()
      assert.deepEqual(view.fields.map(field=>field.key).sort(), expected, event.variant)
      for (const field of view.fields) assert.ok(field.label && ['header','body','context'].includes(field.placement))
    }
  }
  assert.deepEqual([...covered].sort(), [...FAMILIES].sort())
})
