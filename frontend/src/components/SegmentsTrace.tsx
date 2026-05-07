import { useEffect, useMemo, useState } from 'react'
import { formatTimeHM_IST } from '../lib/istFormat'

type SegmentRow = {
  id: string
  start: string
  end: string
  duration_minutes: number
  app: string
  title: string
  zone?: string
  task_name?: string | null
}

type SegmentsPayload = {
  segments?: SegmentRow[]
}

const PROXY = 'http://localhost:5899'

function fmtDur(mins: number): string {
  const m = Math.round(mins)
  const h = Math.floor(m / 60)
  const r = m % 60
  if (h <= 0) return `${r}m`
  if (r === 0) return `${h}h`
  return `${h}h ${r}m`
}

function badgeFor(zoneRaw: string): { text: string; className: string } {
  const z = (zoneRaw || '').toLowerCase()
  if (z === 'meeting') return { text: 'meeting', className: 'bg-[#0c2340] text-[#378add]' }
  if (z === 'untracked_work') return { text: 'untracked', className: 'bg-[#2a1800] text-[#ba7517]' }
  // task_linked (and everything else) uses neutral gray
  return { text: 'task', className: 'bg-[#1a1a1a] text-[#9ca3af]' }
}

interface SegmentsTraceProps {
  date: string
}

export function SegmentsTrace({ date }: SegmentsTraceProps) {
  const [payload, setPayload] = useState<SegmentsPayload | null>(null)
  const [empty, setEmpty] = useState(false)

  useEffect(() => {
    const ac = new AbortController()
    const run = async () => {
      setEmpty(false)
      const url = `${PROXY}/segments?date=${encodeURIComponent(date)}`
      const r = await fetch(url, { signal: ac.signal })
      if (!r.ok) {
        setPayload({ segments: [] })
        setEmpty(true)
        return
      }
      const data = (await r.json().catch(() => null)) as SegmentsPayload | null
      setPayload(data && typeof data === 'object' ? data : { segments: [] })
    }
    run()
    return () => ac.abort()
  }, [date])

  const segments = useMemo(() => {
    const s = Array.isArray(payload?.segments) ? payload!.segments! : []
    return s.slice().sort((a, b) => String(a.start).localeCompare(String(b.start)))
  }, [payload])

  return (
    <section className="min-h-0 flex-1 overflow-y-auto pb-8 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
      <h2 className="mb-3 font-sans text-[11px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
        SEGMENTS — {segments.length} TODAY
      </h2>

      {(empty || segments.length === 0) && (
        <p className="font-mono text-[12px] text-[#3a3a3a]">No segments yet — pipeline running every 30 mins</p>
      )}

      <ul>
        {segments.map((seg) => {
          const badge = badgeFor(String(seg.zone || 'task_linked'))
          const title = (seg.title || '—').trim() || '—'
          const tn = (seg.task_name || '').trim()
          const subtitle = tn ? `${seg.app} · ${tn}` : `${seg.app}`
          const dur = fmtDur(Number(seg.duration_minutes) || 0)
          const clock = `${formatTimeHM_IST(seg.start)} – ${formatTimeHM_IST(seg.end)}`

          return (
            <li key={seg.id} className="border-b border-[#1e1e1e] bg-transparent py-[10px]">
              <div className="flex items-center gap-3 transition-colors hover:mx-[-8px] hover:rounded-md hover:bg-[#0f0f0f] hover:px-2">
                <span
                  className={`shrink-0 rounded-[3px] px-[6px] py-[2px] font-sans text-[10px] font-medium uppercase tracking-wide ${badge.className}`}
                >
                  {badge.text}
                </span>

                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13px] font-medium text-[#d4d4d4]">{title}</p>
                  <p className="mt-0.5 truncate text-[11px] text-[#4b4b4b]">{subtitle}</p>
                </div>

                <div className="shrink-0 text-right">
                  <p className="text-[13px] font-medium text-[#d4d4d4]">{dur}</p>
                  <p className="mt-0.5 font-mono text-[10px] text-[#4b4b4b]">{clock}</p>
                </div>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}

