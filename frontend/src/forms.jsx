import { useState } from 'react'
import { pickFolder } from './api'
import { CloseIcon, FolderIcon } from './icons'

// actor sentinels are @-typed — a NODE may legitimately be named "user"/"system"
export const USER = '@user'

export function HireForm({ parent, op, close }) {
  const [name, setName] = useState('')
  const [tier, setTier] = useState('haiku')
  const [grant, setGrant] = useState(0)
  return (
    <form className="inline-form" onSubmit={(e) => {
      e.preventDefault()
      op({ op: 'hire', parent, tier, grant: +grant, name }).then(close).catch(() => {})
    }}>
      <span className="dim">hire under {parent ?? 'the user (top level)'}:</span>
      <input autoFocus placeholder="name (1–2 words)" value={name}
        onChange={(e) => setName(e.target.value)} required />
      <select value={tier} onChange={(e) => setTier(e.target.value)}>
        <option value="haiku">haiku · 1</option>
        <option value="sonnet">sonnet · 3</option>
        <option value="opus">opus · 5</option>
        <option value="fable">fable · 10</option>
      </select>
      <input type="number" min="0" step="1" value={grant} title="grant"
        onChange={(e) => setGrant(e.target.value)} style={{ width: '4.5em' }} />
      <button type="submit" className="primary">hire</button>
      <button type="button" onClick={close}>cancel</button>
    </form>
  )
}

export function MoveForm({ node, map, op, close }) {
  const [target, setTarget] = useState('')
  const ancestorsOf = (id) => {
    const out = []
    let cur = map.get(id)?.parent
    while (cur != null) { out.push(cur); cur = map.get(cur)?.parent }
    return out
  }
  const candidates = [...map.values()].filter((n) =>
    n.id !== node.id && n.state === 'live' && !ancestorsOf(n.id).includes(node.id)
      && n.id !== map.get(node.id)?.parent)
  const submit = () => {
    const anc = ancestorsOf(node.id)
    const toTop = target === USER
    const body = toTop || anc.includes(target)
      ? { op: 'promote', node: node.id, new_parent: toTop ? null : target }
      : { op: 'demote', node: node.id, new_parent: target }
    op(body).then(close).catch(() => {})
  }
  return (
    <div className="inline-form">
      <span className="dim">move {node.id} under:</span>
      <select value={target} onChange={(e) => setTarget(e.target.value)}>
        <option value="" disabled>choose new parent…</option>
        {map.get(node.id)?.parent !== null &&
          <option value={USER}>· top level (the user) ·</option>}
        {candidates.map((c) => <option key={c.id} value={c.id}>{c.id}</option>)}
      </select>
      <button className="primary" disabled={!target} onClick={submit}>move</button>
      <button onClick={close}>cancel</button>
    </div>
  )
}

export function DirList({ dirs, onChange }) {
  return (
    <div className="dirlist">
      {dirs.map((d, i) => (
        <div className="dirrow" key={i}>
          <input placeholder="E:\path\to\existing\folder" value={d}
            onChange={(e) => onChange(dirs.map((x, j) => (j === i ? e.target.value : x)))} />
          <button type="button" className="iconbtn" title="browse for a folder"
            onClick={() => pickFolder().then((r) => {
              if (r.path) onChange(dirs.map((x, j) => (j === i ? r.path : x)))
            }).catch(() => {})}><FolderIcon fontSize="inherit" /></button>
          <button type="button" className="iconbtn" title="remove folder"
            onClick={() => onChange(dirs.filter((_, j) => j !== i))}><CloseIcon fontSize="inherit" /></button>
        </div>
      ))}
      <div className="dirrow">
        <button type="button" className="addrow"
          onClick={() => onChange([...dirs, ''])}>+ add folder</button>
        <button type="button" className="addrow" title="browse for a folder to add"
          onClick={() => pickFolder().then((r) => {
            if (r.path) onChange([...dirs, r.path])
          }).catch(() => {})}><FolderIcon fontSize="inherit" /> browse</button>
      </div>
    </div>
  )
}
