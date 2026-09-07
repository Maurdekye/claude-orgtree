import { useCallback, useEffect, useRef, useState } from 'react'
import { GitWorkspace } from '../GitWorkspace'
import { closeIfCentred, pinModal, raiseModal, readModalPins, unpinModal } from '../canvas/modalpin'
import type { RefRoutes } from '../canvas/reflinks'
import type { ToastFn } from '../types'
import { noteActionDocument, openSurfaces } from '../windowlife'
import type { GitContext } from './types'

export interface GitPanel {
  id: string
  kind: string
  context: GitContext
  initialRepository?: string
  extra?: boolean
}

/** Presentation IDs never depend on the repository: changing a selection
 * must not remount its panel or give it another panel's pin geometry. */
export function useGitPanels(slug: string | null) {
  const [panels, setPanels] = useState<GitPanel[]>([])
  const current = useRef(panels)
  current.current = panels
  const open = useCallback((context: GitContext) => {
    const id = `git:${context.slug}`
    const detached = openSurfaces().find(p => p.kind === id)
    detached?.window.focus()
    raiseModal(id)
    setPanels(old => {
      const existing = old.find(p => p.id === id)
      return existing ? old.map(p => p.id === id ? { ...p, context } : p)
        : [...old, { id, kind: id, context }]
    })
  }, [])
  const close = useCallback((id: string) => {
    const panel = current.current.find(p => p.id === id)
    if (panel?.extra) unpinModal(panel.kind)
    setPanels(old => old.filter(p => p.id !== id))
  }, [])
  const another = useCallback((source: GitPanel, repository?: string) => {
    const id = `git:${source.context.slug}:panel:${crypto.randomUUID()}`
    const w = window.innerWidth, h = window.innerHeight
    const half = Math.max(320, (w - 48) / 2)
    let x = Math.min(w - half - 16, 48), y = 76
    const sourcePin = readModalPins()[source.kind]
    if (!sourcePin && !openSurfaces().some(p => p.kind === source.kind)) {
      // The first comparison becomes two ordinary draggable desk panels.
      pinModal(source.kind, { x: 16, y: 60, w: half, h: Math.max(240, h - 84) })
      x = 32 + half; y = 60
    } else if (sourcePin) {
      const rect = sourcePin.rect
      const right = rect.x + rect.w + 16
      x = right + half <= w - 8 ? right : rect.x - half - 16 >= 8 ? rect.x - half - 16 : x
      y = rect.y
    }
    pinModal(id, { x, y, w: half, h: Math.max(240, h - 84) })
    // This action explicitly opens another panel on the main desk, even
    // when its source panel currently lives in a separate browser window.
    noteActionDocument(document)
    setPanels(old => [...old, { id, kind: id, context: { slug: source.context.slug }, initialRepository: repository, extra: true }])
  }, [])
  useEffect(() => {
    for (const panel of current.current) if (panel.context.slug !== slug && panel.extra) unpinModal(panel.kind)
    setPanels(old => old.filter(p => p.context.slug === slug))
  }, [slug])
  useEffect(() => () => {
    for (const panel of current.current) if (panel.extra) unpinModal(panel.kind)
  }, [])
  return { panels: panels.filter(p => p.context.slug === slug), open, close, another }
}

export function GitPanels({ panels, close, another, routes, toast }: {
  panels: GitPanel[]; close: (id: string) => void
  another: (source: GitPanel, repository?: string) => void
  routes: RefRoutes; toast: ToastFn
}) {
  return <>{panels.map(panel => <GitWorkspace key={panel.id} slug={panel.context.slug}
    panelId={panel.kind} initialRepository={panel.initialRepository} context={panel.context}
    onOpenPanel={repository => another(panel, repository)}
    routes={{ ...routes, onOpen: ref => {
      closeIfCentred(panel.kind, () => close(panel.id))
      routes.onOpen(ref)
    } }} toast={toast} close={() => close(panel.id)} />)}</>
}
