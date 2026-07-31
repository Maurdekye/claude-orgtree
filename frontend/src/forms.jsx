import { pickFolder } from './picker'
import { CloseIcon, FolderIcon } from './icons'

// (№45: HireForm and MoveForm removed — dead since the draft-card hire flow
// and the drag/orgtree_move gestures replaced them; nothing imported either.)

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
