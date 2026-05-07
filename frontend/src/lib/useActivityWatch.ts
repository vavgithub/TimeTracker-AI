import { useCallback, useEffect, useRef, useState } from 'react'
import { todayISOInIST } from './istFormat'

/** Default Trace proxy (python proxy.py on 5899). Set VITE_TRACE_PROXY_URL to override or empty to use VITE_AW_URL. */
const _proxy = (import.meta.env.VITE_TRACE_PROXY_URL as string | undefined)
const TRACE_PROXY =
  _proxy === ''
    ? null
    : (_proxy ?? 'http://localhost:5899').replace(/\/$/, '')
const AW_BASE = TRACE_PROXY
  ? `${TRACE_PROXY}/aw`
  : (import.meta.env.VITE_AW_URL as string | undefined) || 'http://localhost:5600/api/0'

const ENV_WINDOW_BUCKET = import.meta.env.VITE_AW_WINDOW_BUCKET as string | undefined
const ENV_INPUT_BUCKET = import.meta.env.VITE_AW_INPUT_BUCKET as string | undefined

type BucketId = string

function pickBucketId(
  ids: string[],
  prefix: string,
  preferred?: string | undefined
): BucketId | null {
  const cleanedPreferred = (preferred ?? '').trim()
  if (cleanedPreferred && ids.includes(cleanedPreferred)) return cleanedPreferred
  const match = ids.find((x) => x.startsWith(prefix))
  return match ?? null
}

function bucketEventsUrl(bucket: string, params: Record<string, string>) {
  const q = new URLSearchParams(params).toString()
  return `${AW_BASE}/buckets/${encodeURIComponent(bucket)}/events?${q}`
}

async function fetchBucketIds(): Promise<string[] | null> {
  try {
    const r = await fetch(`${AW_BASE}/buckets/`)
    if (!r.ok) return null
    const j = (await r.json()) as Record<string, unknown>
    return Object.keys(j ?? {})
  } catch {
    return null
  }
}

export interface WindowLiveState {
  app: string
  title: string
  eventStartMs: number | null
}

export interface InputLiveState {
  keystrokes: number
  mouseClicks: number
  scrollUnits: number
}

export function useActivityWatch(enabled: boolean) {
  const [windowState, setWindowState] = useState<WindowLiveState>({
    app: '—',
    title: '—',
    eventStartMs: null,
  })
  const [inputState, setInputState] = useState<InputLiveState>({
    keystrokes: 0,
    mouseClicks: 0,
    scrollUnits: 0,
  })
  const [elapsedSec, setElapsedSec] = useState(0)
  const [awWindowOk, setAwWindowOk] = useState<boolean | null>(null)
  const [awInputOk, setAwInputOk] = useState<boolean | null>(null)
  const eventStartRef = useRef<number | null>(null)
  const bucketsRef = useRef<{ window: BucketId | null; input: BucketId | null } | null>(
    null
  )

  useEffect(() => {
    eventStartRef.current = windowState.eventStartMs
  }, [windowState.eventStartMs])

  const ensureBuckets = useCallback(async () => {
    if (bucketsRef.current) return bucketsRef.current
    const ids = await fetchBucketIds()
    if (!ids || ids.length === 0) {
      bucketsRef.current = { window: null, input: null }
      return bucketsRef.current
    }
    const window = pickBucketId(ids, 'aw-watcher-window_', ENV_WINDOW_BUCKET)
    const input = pickBucketId(ids, 'aw-watcher-input_', ENV_INPUT_BUCKET)
    bucketsRef.current = { window, input }
    return bucketsRef.current
  }, [])

  const pollWindow = useCallback(async () => {
    try {
      const { window } = await ensureBuckets()
      if (!window) {
        setAwWindowOk(false)
        return
      }
      const url = bucketEventsUrl(window, { limit: '100' })
      const r = await fetch(url)
      if (!r.ok) {
        setAwWindowOk(false)
        return
      }
      const events = (await r.json()) as Array<{
        timestamp: string
        duration: number
        data?: { app?: string; title?: string }
      }>
      if (!Array.isArray(events) || events.length === 0) {
        setAwWindowOk(true)
        return
      }
      const last = events.reduce((a, b) =>
        new Date(a.timestamp) > new Date(b.timestamp) ? a : b
      )
      const app = last.data?.app ?? 'unknown'
      const title = last.data?.title ?? '—'
      const startMs = new Date(last.timestamp).getTime()
      setWindowState({ app, title, eventStartMs: startMs })
      setAwWindowOk(true)
    } catch {
      setAwWindowOk(false)
    }
  }, [])

  const pollInput = useCallback(async () => {
    const day = todayISOInIST()
    const start = `${day}T00:00:00+05:30`
    try {
      const { input } = await ensureBuckets()
      if (!input) {
        setAwInputOk(false)
        return
      }
      const url = bucketEventsUrl(input, {
        start,
        limit: '2000',
      })
      const r = await fetch(url)
      if (!r.ok) {
        setAwInputOk(false)
        return
      }
      const events = (await r.json()) as Array<{
        data?: { presses?: number; clicks?: number; scrollY?: number }
      }>
      if (!Array.isArray(events)) {
        setAwInputOk(false)
        return
      }
      let k = 0
      let c = 0
      let s = 0
      for (const e of events) {
        k += e.data?.presses ?? 0
        c += e.data?.clicks ?? 0
        s += e.data?.scrollY ?? 0
      }
      setInputState({ keystrokes: k, mouseClicks: c, scrollUnits: s })
      setAwInputOk(true)
    } catch {
      setAwInputOk(false)
    }
  }, [ensureBuckets])

  useEffect(() => {
    if (!enabled) return
    const t0 = window.setTimeout(() => void pollWindow(), 0)
    const w = window.setInterval(() => void pollWindow(), 5_000)
    return () => {
      window.clearTimeout(t0)
      window.clearInterval(w)
    }
  }, [enabled, pollWindow])

  useEffect(() => {
    if (!enabled) return
    const t0 = window.setTimeout(() => void pollInput(), 0)
    const w = window.setInterval(() => void pollInput(), 30_000)
    return () => {
      window.clearTimeout(t0)
      window.clearInterval(w)
    }
  }, [enabled, pollInput])

  useEffect(() => {
    if (!enabled) return
    const tick = () => {
      const t = eventStartRef.current
      setElapsedSec(t != null ? Math.max(0, Math.floor((Date.now() - t) / 1000)) : 0)
    }
    const t0 = window.setTimeout(tick, 0)
    const i = window.setInterval(tick, 1000)
    return () => {
      window.clearTimeout(t0)
      window.clearInterval(i)
    }
  }, [enabled])

  const awLive = awWindowOk === true && awInputOk === true
  const awOffline =
    awWindowOk === false && awInputOk === false
      ? true
      : awWindowOk === false || awInputOk === false
        ? 'partial'
        : false

  return {
    windowState,
    inputState,
    elapsedSec,
    awBase: AW_BASE,
    awWindowOk,
    awInputOk,
    awOffline,
    awLive,
    usingProxy: TRACE_PROXY != null,
  }
}
