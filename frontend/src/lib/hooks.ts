import { useCallback, useEffect, useRef, useState } from 'react'

/** Load-once-with-refresh. Keeps the previous value visible while refetching so
 *  the layout does not collapse to a spinner on every poll. */
export function useAsync<T>(
  loader: () => Promise<T>,
  deps: unknown[] = [],
): { data: T | null; error: string | null; loading: boolean; reload: () => void } {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const alive = useRef(true)

  useEffect(() => {
    alive.current = true
    return () => {
      alive.current = false
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    loader()
      .then((value) => {
        if (!cancelled && alive.current) {
          setData(value)
          setError(null)
        }
      })
      .catch((err: unknown) => {
        if (!cancelled && alive.current) {
          setError(err instanceof Error ? err.message : String(err))
        }
      })
      .finally(() => {
        if (!cancelled && alive.current) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  const reload = useCallback(() => setNonce((n) => n + 1), [])
  return { data, error, loading, reload }
}

/** Calls `tick` on an interval while `active` is true. Used for job progress. */
export function useInterval(tick: () => void, ms: number, active: boolean): void {
  const saved = useRef(tick)
  useEffect(() => {
    saved.current = tick
  }, [tick])

  useEffect(() => {
    if (!active) return
    const id = window.setInterval(() => saved.current(), ms)
    return () => window.clearInterval(id)
  }, [ms, active])
}

export function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(id)
  }, [value, ms])
  return debounced
}

export type Route = { view: string; param?: string }

function parseHash(): Route {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const [view, param] = raw.split('/')
  return { view: view || 'showcase', param: param || undefined }
}

/** Hash routing, so any fact or relation is directly linkable. */
export function useHashRoute(): [Route, (view: string, param?: string) => void] {
  const [route, setRoute] = useState<Route>(parseHash)

  useEffect(() => {
    const onChange = () => setRoute(parseHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  const navigate = useCallback((view: string, param?: string) => {
    window.location.hash = param ? `/${view}/${param}` : `/${view}`
  }, [])

  return [route, navigate]
}

/** Escape-to-close for the evidence drawer. */
export function useEscape(handler: () => void, active: boolean): void {
  useEffect(() => {
    if (!active) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') handler()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [handler, active])
}
