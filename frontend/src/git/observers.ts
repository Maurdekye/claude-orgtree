import * as api from './api'

type Observation = Awaited<ReturnType<typeof api.getGitObservation>>
type Listener = { value: (value: Observation) => void; error: (error: unknown) => void }
type Watch = { listeners: Set<Listener>; timer: ReturnType<typeof setInterval>; running: boolean; lastWatch: number; poll: () => Promise<void> }
const watches = new Map<string, Watch>()

/** One poll per visible repository, shared by pinned and popped-out panels. */
export function observeGit(slug: string, rid: string, listener: Listener) {
  const key = JSON.stringify([slug, rid])
  let watch = watches.get(key)
  if (!watch) {
    const next = { listeners: new Set<Listener>(), running: false, lastWatch: -Infinity } as Watch
    next.poll = async () => {
      if (next.running || !next.listeners.size) return
      next.running = true
      try {
        const value = await api.getGitObservation(slug, rid)
        for (const target of next.listeners) target.value(value)
        if (value.freshness?.watched === false) next.lastWatch = -Infinity
        if (value.freshness?.watched && next.listeners.size && performance.now() - next.lastWatch >= 30_000) {
          next.lastWatch = performance.now()
          await api.watchGit(slug, rid)
        }
      } catch (error) { for (const target of next.listeners) target.error(error) }
      finally { next.running = false }
    }
    next.timer = setInterval(() => void next.poll(), 5000)
    watches.set(key, next); watch = next
  }
  watch.listeners.add(listener)
  return () => {
    watch.listeners.delete(listener)
    if (!watch.listeners.size) { clearInterval(watch.timer); watches.delete(key) }
  }
}

/** App settings changes immediately refresh every mounted Git panel. */
export function refreshGitPreferences() {
  for (const watch of watches.values()) { watch.lastWatch = -Infinity; void watch.poll() }
}
