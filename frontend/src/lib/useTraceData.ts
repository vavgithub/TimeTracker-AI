import { useCallback, useEffect, useState } from 'react'
import type { DailyJsonTrace, TraceSession, WriterSessionRaw } from './trace'

let _proxy = import.meta.env.VITE_TRACE_PROXY_URL as string | undefined
if (_proxy === '') _proxy = undefined
const TRACE_BASE = (_proxy ?? 'http://localhost:5899').replace(/\/$/, '')

const REFRESH_MS = 30_000

function normalizeSession(raw: WriterSessionRaw): TraceSession {
  const ai = raw.ai_enrichment ?? {}
  const inp = raw.input ?? {}
  return {
    session_id: raw.session_id,
    start: raw.start,
    end: raw.end,
    duration_min: Number(raw.duration_min) || 0,
    zone: (ai.zone as TraceSession['zone']) || 'unknown',
    clickup_task_id: ai.clickup_task_id ?? null,
    clickup_task_name: ai.clickup_task_name ?? null,
    map_confidence: Number(ai.map_confidence) || 0,
    map_method: ai.map_method || 'none',
    map_notes: ai.map_notes,
    input: {
      keystrokes: Number(inp.keystrokes) || 0,
      mouse_clicks: Number(inp.mouse_clicks) || 0,
      activity_rate: Number(inp.activity_rate) || 0,
      scroll_units: Number(inp.scroll_units) || 0,
    },
    apps: raw.apps ?? [],
    titles: raw.titles ?? [],
    urls: raw.urls ?? [],
    app_breakdown: Array.isArray(raw.app_breakdown) ? raw.app_breakdown : [],
  }
}

export interface TraceDataState {
  daily: DailyJsonTrace | null
  sessions: TraceSession[]
  error: string | null
  loading: boolean
  lastFetchedAt: number | null
}

export function useTraceData(date: string): TraceDataState & { refetch: () => void } {
  const [daily, setDaily] = useState<DailyJsonTrace | null>(null)
  const [sessions, setSessions] = useState<TraceSession[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [lastFetchedAt, setLastFetchedAt] = useState<number | null>(null)

  const load = useCallback(
    async (mode: 'full' | 'quiet' = 'full') => {
      const showSpinner = mode === 'full'
      if (showSpinner) setLoading(true)
      setError(null)
      const dailyUrl = `${TRACE_BASE}/out/daily_${date}.json`
      const sessionsUrl = `${TRACE_BASE}/out/sessions_${date}.json`

      try {
        const [dr, sr] = await Promise.all([fetch(dailyUrl), fetch(sessionsUrl)])
        if (!dr.ok) {
          setDaily(null)
          setSessions([])
          setError(dr.status === 404 ? 'not_found' : `daily ${dr.status}`)
          setLastFetchedAt(Date.now())
          return
        }
        const d = (await dr.json()) as DailyJsonTrace
        setDaily(d)

        if (sr.ok) {
          const list = (await sr.json()) as WriterSessionRaw[]
          const norm = Array.isArray(list) ? list.map(normalizeSession) : []
          norm.sort((a, b) => a.start.localeCompare(b.start))
          setSessions(norm)
        } else {
          setSessions([])
        }
        setLastFetchedAt(Date.now())
      } catch {
        setDaily(null)
        setSessions([])
        setError('network')
      } finally {
        if (showSpinner) setLoading(false)
      }
    },
    [date],
  )

  useEffect(() => {
    void load('full')
  }, [load])

  useEffect(() => {
    const id = window.setInterval(() => void load('quiet'), REFRESH_MS)
    return () => window.clearInterval(id)
  }, [load])

  return { daily, sessions, error, loading, lastFetchedAt, refetch: load }
}
