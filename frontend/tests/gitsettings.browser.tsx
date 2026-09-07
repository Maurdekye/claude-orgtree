import { createRoot } from 'react-dom/client'
import { AccountsPanel } from '../src/canvas/accounts'
import { observeGit } from '../src/git/observers'
import '../src/styles.css'

const seen: boolean[][] = [[], []]
Object.assign(window, { gitSettingsSeen: seen })
for (let i = 0; i < 2; i++) observeGit('fixture', 'repo', {
  value: value => seen[i]!.push(value.freshness?.watched ?? false), error: e => { throw e },
})
createRoot(document.getElementById('root')!).render(<AccountsPanel toast={() => {}} close={() => {}} />)
