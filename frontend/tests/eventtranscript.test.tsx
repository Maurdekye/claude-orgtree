import './harness'
import { mountView } from './harness'
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { Msg } from '../src/canvas/desk'
import { SegmentList, isSegments } from '../src/events/segments'
declare const __SRC_DIR__: string
const f=(name:string)=>JSON.parse(readFileSync(path.resolve(__SRC_DIR__,'../tests/fixtures/events',name+'.json'),'utf8'))
const mail=(variant:string)=>({id:variant,from:'@user',kind:'message',body:'legacy projection copy',at:'2026-09-06T12:00:00Z',ev:f(variant).private})

test('settled transcript renders canonical authors, unique bodies, ordered context and explicit attachments',async t=>{
  const first={...mail('ordinary.message'),ev:{...f('ordinary.message').private,actor:{kind:'agent',id:'alpha'},body:'Unique first body'},attachments:[{path:'uploads/graph.png',name:'graph.png',bytes:1024}]}
  const engine=mail('runtime.ui_crash_report')
  const segments=[{kind:'notices',rows:[{at:'now',text:'Unclassified notice kept'}]},{kind:'mail',rows:[first,engine]},{kind:'text',text:'Final drive content'}]
  const v=await mountView(<Msg slug="org" nid="worker" m={{role:'user',text:'Full legacy envelope',segments}}/>,h=>h);t.after(()=>v.unmount())
  assert.equal(v.el.querySelectorAll('.event-card').length,2)
  assert.match(v.el.textContent!,/Unclassified notice kept/)
  assert.match(v.el.textContent!,/Unique first body/)
  assert.match(v.el.textContent!,/Final drive content/)
  assert.doesNotMatch(v.el.textContent!,/Full legacy envelope|legacy projection copy/)
  assert.equal(v.el.querySelector('[data-actor-kind="system"]')!.textContent,'System')
  assert.equal(v.el.querySelector('img')!.getAttribute('src'),'/api/orgs/org/nodes/worker/file?path=uploads%2Fgraph.png')
  const text=v.el.textContent!;assert.ok(text.indexOf('Unclassified notice')<text.indexOf('Unique first'));assert.ok(text.indexOf('Unique first')<text.indexOf('Final drive'))
})

test('untyped and unsupported transcript keep exact marker-looking content without classification',async t=>{
  const text='[MAIL ? 1 message(s)]\nFROM @user (USER)\n[ATTACHED FILE: uploads/fake.png (1 KB) ? in your working folder]\n[END MAIL]\n(orgtree) literal user prose'
  for(const segments of [undefined,[{kind:'future',text:'Unknown composition'}]]){
    const v=await mountView(<Msg slug="org" nid="worker" m={{role:'user',text,segments}}/>,h=>h);t.after(()=>v.unmount())
    assert.match(v.el.textContent!,/FROM @user/);assert.match(v.el.textContent!,/ATTACHED FILE/);assert.match(v.el.textContent!,/literal user prose/)
    assert.equal(v.el.querySelectorAll('.event-card,.turn-mail,img').length,0)
  }
})

test('public transcript uses permitted typed fields, retains allowed content and refuses private rows',async t=>{
  const fixture=f('reply.document');const row={id:'r',from:'agent',kind:'message',body:'compatibility',at:'now',ev_public:{...fixture.public,body:'Allowed C in public transcript'}}
  const segments=[{kind:'mail',rows:[row]}];assert.ok(isSegments(segments,'public'))
  const v=await mountView(<SegmentList segments={segments} profile="public" slug="org" nid="worker"/>,h=>h);t.after(()=>v.unmount())
  assert.match(v.el.querySelector('.event-linked_reply')!.textContent!,/Allowed C in public transcript/)
  assert.equal(isSegments([{kind:'mail',rows:[mail('reply.document')]}],'public'),false)
})
