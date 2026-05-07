import { useEffect, useMemo, useState } from 'react'

function fmtHm(mins: number): string {
  const m = Math.round(mins)
  const h = Math.floor(m / 60)
  const r = m % 60
  if (h <= 0) return `${r}m`
  if (r === 0) return `${h}h`
  return `${h}h ${r}m`
}

type SegmentRow = {
  id: string
  start: string
  end: string
  duration_minutes: number
  app: string
  title: string
  zone?: string
  task_id?: string | null
  task_name?: string | null
}

type SegmentsPayload = {
  segments?: SegmentRow[]
  daily_summary?: {
    by_task?: Record<string, number>
    total_meeting_minutes?: number
    total_task_minutes?: number
    total_untracked_minutes?: number
  }
}

interface DayTimelineProps {
  date: string
}

const ROW_H = 28
const ROW_GAP = 4
const BAR_H = 6
const BAR_RADIUS = 3
const NAME_W = 120
const DUR_W = 50

const EM_DASH_NAME_SEP = ' \u2014 '

/** by_task key may be "Parent — Subtask": show subtask when present, else parent; full name if no em dash. */
function displayNameFromByTask(name: string): string {
  if (!name.includes(EM_DASH_NAME_SEP)) return name
  const [part1, part2] = name.split(EM_DASH_NAME_SEP, 2)
  if (part2 == null) return name
  if (part2.trim() === '') return part1.trim()
  return part2.trim()
}

function normalizeName(s: string): string {
  return s.trim().toLowerCase()
}

/** Dominant zone per task name from segment rows (minutes-weighted). */
function zoneMinutesByTaskName(segments: SegmentRow[]): Map<string, Map<string, number>> {
  const out = new Map<string, Map<string, number>>()
  for (const s of segments) {
    const raw = (s.task_name ?? '').trim()
    if (!raw) continue
    const d = Number(s.duration_minutes) || 0
    if (d <= 0) continue
    const z = String(s.zone ?? 'task_linked').toLowerCase()
    let m = out.get(raw)
    if (!m) {
      m = new Map()
      out.set(raw, m)
    }
    m.set(z, (m.get(z) ?? 0) + d)
  }
  return out
}

function dominantZoneForTask(
  taskKey: string,
  zoneMinutes: Map<string, Map<string, number>>,
): 'meeting' | 'task_linked' | 'untracked' {
  let minutes = zoneMinutes.get(taskKey)
  if (!minutes) {
    const n = normalizeName(taskKey)
    for (const [k, v] of zoneMinutes.entries()) {
      if (normalizeName(k) === n) {
        minutes = v
        break
      }
    }
  }
  if (!minutes || minutes.size === 0) return 'task_linked'

  let bestZ = 'task_linked'
  let bestM = -1
  for (const [z, m] of minutes) {
    if (m > bestM) {
      bestM = m
      bestZ = z
    }
  }
  const z = bestZ.toLowerCase()
  if (z === 'meeting') return 'meeting'
  if (z === 'untracked') return 'untracked'
  return 'task_linked'
}

function barStyle(zone: 'meeting' | 'task_linked' | 'untracked'): React.CSSProperties {
  if (zone === 'meeting') {
    return { background: 'var(--color-background-info, #1d4ed8)' }
  }
  if (zone === 'untracked') {
    return { background: 'var(--color-background-warning, #b45309)' }
  }
  return {
    background: 'color-mix(in srgb, var(--color-text-secondary, #a3a3a3) 30%, transparent)',
  }
}

export function DayTimeline({ date }: DayTimelineProps) {
  const [payload, setPayload] = useState<SegmentsPayload | null>(null)

  useEffect(() => {
    const ac = new AbortController()
    const run = async () => {
      const url = `http://localhost:5899/segments?date=${encodeURIComponent(date)}`
      const r = await fetch(url, { signal: ac.signal })
      if (!r.ok) {
        setPayload(null)
        return
      }
      const data = (await r.json().catch(() => null)) as SegmentsPayload | null
      setPayload(data && typeof data === 'object' ? data : null)
    }
    run()
    return () => ac.abort()
  }, [date])

  const segments = useMemo(() => (Array.isArray(payload?.segments) ? payload!.segments! : []), [payload])
  const byTask = useMemo(() => payload?.daily_summary?.by_task ?? {}, [payload])

  const zoneMinutes = useMemo(() => zoneMinutesByTaskName(segments), [segments])

  const totals = useMemo(() => {
    const active = segments.reduce((acc, s) => acc + (Number(s.duration_minutes) || 0), 0)
    const meet = segments.reduce(
      (acc, s) => acc + ((String(s.zone || '').toLowerCase() === 'meeting' ? Number(s.duration_minutes) || 0 : 0) || 0),
      0,
    )
    const deep = segments.reduce(
      (acc, s) =>
        acc + ((String(s.zone || '').toLowerCase() === 'task_linked' ? Number(s.duration_minutes) || 0 : 0) || 0),
      0,
    )
    return { active, meet, deep }
  }, [segments])

  const taskRows = useMemo(() => {
    const rows = Object.entries(byTask)
      .map(([name, minutes]) => ({
        name,
        displayName: displayNameFromByTask(name),
        minutes: Number(minutes) || 0,
        zone: dominantZoneForTask(name, zoneMinutes),
      }))
      .filter((x) => x.minutes > 0)
      .sort((a, b) => b.minutes - a.minutes)
      .slice(0, 8)
    const maxM = rows.reduce((m, r) => Math.max(m, r.minutes), 0) || 1
    return rows.map((r) => ({ ...r, barPct: (r.minutes / maxM) * 100 }))
  }, [byTask, zoneMinutes])

  if (payload == null) {
    return (
      <div className="mb-3 rounded-md border border-[#1e1e1e] bg-transparent p-3">
        <h3 className="font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">Day timeline</h3>
        <p className="mt-2 font-mono text-[11px] text-[#3a3a3a]">
          No segments for {date} yet (run `python3 main.py --date {date} --write-out` to generate `out/segments_{date}.json`).
        </p>
      </div>
    )
  }

  return (
    <div className="mb-3 rounded-md border border-[#1e1e1e] bg-transparent p-3">
      <div className="mb-2 flex flex-wrap items-start justify-between gap-2">
        <h3 className="font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">Day timeline</h3>
        <p className="text-right font-mono text-[12px] leading-relaxed text-[#6b6b6b]">
          <span className="text-[#d4d4d4]">Active {fmtHm(totals.active)}</span>
          <span className="mx-2 text-[#2a2a2a]">·</span>
          <span className="text-[#d4d4d4]">Meeting {fmtHm(totals.meet)}</span>
          <span className="mx-2 text-[#2a2a2a]">·</span>
          <span className="text-[#6ee7b7]">Deep work {fmtHm(totals.deep)}</span>
        </p>
      </div>

      <div className="flex flex-col" style={{ gap: ROW_GAP }}>
        {taskRows.map((row) => (
          <div
            key={row.name}
            className="flex w-full min-w-0 items-center gap-2"
            style={{ height: ROW_H }}
            title={`${row.displayName} · ${fmtHm(row.minutes)} · ${row.zone}`}
          >
            <div
              className="shrink-0 truncate font-mono text-[12px] text-[#d4d4d4]"
              style={{ width: NAME_W }}
              title={row.displayName}
            >
              {row.displayName}
            </div>
            <div className="flex min-w-0 flex-1 items-center">
              <div
                className="max-w-full"
                style={{
                  width: `${row.barPct}%`,
                  height: BAR_H,
                  borderRadius: BAR_RADIUS,
                  ...barStyle(row.zone),
                }}
              />
            </div>
            <div
              className="shrink-0 text-right font-mono text-[12px] text-[#6b6b6b]"
              style={{ width: DUR_W }}
            >
              {fmtHm(row.minutes)}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
