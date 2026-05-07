import { useEffect, useMemo, useState } from 'react'
import type { DailyJsonTrace, TraceSession } from '../lib/trace'

export interface EmployeeCardProps {
  daily: DailyJsonTrace | null
  sessions: TraceSession[]
}

function fmtMin(mins: number): string {
  const m = Math.round(Number(mins) || 0)
  if (m <= 0) return '0m'
  const h = Math.floor(m / 60)
  const r = m % 60
  if (h <= 0) return `${r}m`
  if (r === 0) return `${h}h`
  return `${h}h ${r}m`
}

function fmtDayLong(isoDate: string): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  if (!y || !m || !d) return isoDate
  const utc = Date.UTC(y, m - 1, d, 12, 0, 0)
  return new Intl.DateTimeFormat('en-US', {
    month: 'long',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'Asia/Kolkata',
  }).format(new Date(utc))
}

export function EmployeeCard({ daily, sessions }: EmployeeCardProps) {
  const [eodData, setEodData] = useState<any>(null)
  const [skillData, setSkillData] = useState<any>(null)
  const [showPayload, setShowPayload] = useState(false)

  const day = daily?.date ?? ''
  const dateLabel = day ? fmtDayLong(day) : '—'

  const totals = daily?.totals
  const sessionCount = Number(totals?.session_count) || 0

  useEffect(() => {
    if (!daily?.date) return
    // Try today first, fall back to yesterday
    const yesterday = new Date()
    yesterday.setDate(yesterday.getDate() - 1)
    const yDate = yesterday.toISOString().split('T')[0]

    const tryLoad = async (d: string) => {
      const r = await fetch(`http://localhost:5899/out/eod_${d}.json`)
      if (r.ok) return r.json()
      return null
    }

    tryLoad(daily.date).then((d) => {
      if (d) {
        setEodData(d)
        return
      }
      tryLoad(yDate).then((d2) => setEodData(d2))
    })
  }, [daily?.date])

  useEffect(() => {
    // Fetch skill profile for latest available week
    // Try last 4 Mondays
    const mondays: string[] = []
    const d = new Date()
    while (mondays.length < 4) {
      if (d.getDay() === 1) mondays.push(d.toISOString().split('T')[0])
      d.setDate(d.getDate() - 1)
    }
    const tryMondays = async () => {
      for (const m of mondays) {
        const r = await fetch(`http://localhost:5899/out/skill_profile_${m}.json`)
        if (r.ok) {
          setSkillData(await r.json())
          return
        }
      }
    }
    tryMondays()
  }, [])

  const activeMin = Number(eodData?.productivity?.active_minutes ?? totals?.active_minutes ?? 0) || 0
  const deepMin = Number(eodData?.computed?.deep_work_minutes ?? totals?.task_linked_minutes ?? 0) || 0
  const meetingMin = Number(eodData?.productivity?.meeting_minutes ?? totals?.meeting_minutes ?? 0) || 0
  const activityRate =
    Number(eodData?.productivity?.activity_rate ?? totals?.productivity_pct ?? totals?.activity ?? 0) || 0
  const keys = Number(eodData?.productivity?.keystrokes ?? 0) || 0
  const clicks = Number(eodData?.productivity?.mouse_clicks ?? 0) || 0

  function skillLabel(s: string): string {
    const x = (s || '').trim()
    if (!x) return 'General'
    return x.charAt(0).toUpperCase() + x.slice(1)
  }

  const tasksFromSessions = useMemo(() => {
    const byName = new Map<string, number>()
    for (const s of sessions || []) {
      if (String(s.zone) !== 'task_linked') continue
      const name = (s.clickup_task_name ?? '').trim()
      if (!name) continue
      byName.set(name, (byName.get(name) ?? 0) + (Number(s.duration_min) || 0))
    }
    return [...byName.entries()]
      .map(([name, minutes]) => ({ name, minutes }))
      .sort((a, b) => b.minutes - a.minutes)
      .slice(0, 5)
  }, [sessions])

  const tasksFromEod = useMemo(() => {
    const raw = eodData?.tasks
    if (!Array.isArray(raw)) return null
    const rows = raw
      .filter((t) => t && typeof t === 'object')
      .map((t) => ({
        name: String((t as any).name ?? '').trim(),
        today_minutes: Number((t as any).today_minutes ?? 0) || 0,
        status: String((t as any).status ?? '').trim().toLowerCase(),
        skill_category: String((t as any).skill_category ?? '').trim(),
        is_overdue: Boolean((t as any).is_overdue),
        days_overdue: Number((t as any).days_overdue ?? 0) || 0,
        time_estimate_minutes:
          (t as any).time_estimate_minutes == null ? null : Number((t as any).time_estimate_minutes),
        percent_of_estimate:
          (t as any).percent_of_estimate == null ? null : Number((t as any).percent_of_estimate),
        tools: Array.isArray((t as any).tools) ? ((t as any).tools as any[]) : [],
      }))
      .filter((t) => t.name && t.today_minutes > 0)
      .sort((a, b) => b.today_minutes - a.today_minutes)
    return rows
  }, [eodData])

  const meetingsFromEod = useMemo(() => {
    const raw = eodData?.meetings
    if (!Array.isArray(raw)) return []
    return raw
      .filter((m) => m && typeof m === 'object')
      .map((m) => ({
        name: String((m as any).name ?? '').trim() || 'Meeting',
        minutes: Number((m as any).minutes ?? 0) || 0,
      }))
      .filter((m) => m.minutes > 0)
  }, [eodData])

  const perf =
    eodData?.performance_signals && typeof eodData.performance_signals === 'object'
      ? (eodData.performance_signals as any)
      : null

  const generatedAtLabel = useMemo(() => {
    const raw = skillData?.generated_at
    if (!raw) return '—'
    const dt = new Date(String(raw))
    if (Number.isNaN(dt.getTime())) return '—'
    return new Intl.DateTimeFormat('en-GB', {
      day: '2-digit',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
      timeZone: 'Asia/Kolkata',
    }).format(dt)
  }, [skillData])

  return (
    <div className="rounded-md border border-[#1e1e1e] bg-transparent p-3">
      <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
        Employee card
      </h3>
      <p className="text-[17px] font-medium text-[#e8e8e8]">Khyathi Neerukonda</p>
      <p className="font-mono text-[12px] text-[#6b6b6b]">AI Developer · Value at Void</p>
      <p className="mt-1 font-mono text-[11px] text-[#4b4b4b]">{dateLabel}</p>

      <div className="mt-3">
        <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Day overview
        </h3>
        <div className="flex flex-wrap gap-6">
          <div className="min-w-[120px]">
            <p className="font-mono text-xl text-[#e8e8e8]">{fmtMin(activeMin)}</p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">Active</p>
          </div>
          <div className="min-w-[120px]">
            <p className="font-mono text-xl text-[#6ee7b7]">{fmtMin(deepMin)}</p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">Deep work</p>
          </div>
          <div className="min-w-[120px]">
            <p className="font-mono text-xl text-[#e8e8e8]">{fmtMin(meetingMin)}</p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">Meetings</p>
          </div>
          <div className="min-w-[120px]">
            <p className={`font-mono text-xl ${activityRate >= 80 ? 'text-[#6ee7b7]' : 'text-[#e8e8e8]'}`}>
              {activityRate.toFixed(1)}%
            </p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">Activity</p>
          </div>
        </div>
        {(keys > 0 || clicks > 0) && (
          <p className="mt-2 font-mono text-[11px] text-[#4b4b4b]">
            ⌨ {keys.toLocaleString()} keystrokes · 🖱 {clicks.toLocaleString()} clicks
          </p>
        )}
      </div>

      <div className="mt-3">
        <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Tasks today
        </h3>
        {tasksFromEod && tasksFromEod.length > 0 ? (
          <ul className="border-t border-[#1a1a1a]">
            {tasksFromEod.map((t) => {
              const est = t.time_estimate_minutes
              const pct = t.percent_of_estimate
              const showEst = est != null && Number.isFinite(est)
              const showPct = pct != null && Number.isFinite(pct)
              const pctColor = showPct && (pct as number) > 100 ? 'text-amber-600' : 'text-[#1d9e75]'

              const tools = (t.tools || [])
                .filter((x) => x && typeof x === 'object')
                .map((x) => ({
                  app: String((x as any).app ?? '').trim(),
                  minutes: Number((x as any).minutes ?? 0) || 0,
                }))
                .filter((x) => x.app && x.minutes > 0)
                .slice(0, 2)
              const toolsLine =
                tools.length > 0
                  ? `via ${tools.map((x) => `${x.app} ${Math.round(x.minutes)}m`).join(' · ')}`
                  : ''

              return (
                <li key={t.name} className="border-b border-[#1a1a1a] py-2 last:border-b-0">
                  <div className="flex items-center justify-between gap-3">
                    <span className="max-w-[65%] truncate font-mono text-[12px] text-[#d4d4d4]">
                      {t.name}
                    </span>
                    <span className="shrink-0 font-mono text-[12px] text-[#4b4b4b]">{fmtMin(t.today_minutes)}</span>
                  </div>

                  <div className="mt-1 flex flex-wrap items-center gap-2">
                    {t.skill_category ? (
                      <span className="rounded bg-[#1e1e1e] px-1.5 py-0.5 font-mono text-[10px] text-[#4b4b4b]">
                        {t.skill_category}
                      </span>
                    ) : null}
                    {t.is_overdue ? (
                      <span className="font-mono text-[10px] text-amber-600">⚠ {t.days_overdue}d overdue</span>
                    ) : null}
                    {showEst ? (
                      <span className="font-mono text-[10px] text-[#4b4b4b]">Est: {fmtMin(est as number)}</span>
                    ) : null}
                    {showPct ? (
                      <span className={`font-mono text-[10px] ${pctColor}`}>{(pct as number).toFixed(1)}% used</span>
                    ) : null}
                  </div>

                  {toolsLine && <p className="mt-1 font-mono text-[10px] text-[#3a3a3a]">{toolsLine}</p>}
                </li>
              )
            })}
          </ul>
        ) : tasksFromSessions.length === 0 ? (
          <p className="font-mono text-[11px] text-[#3a3a3a]">No mapped tasks yet</p>
        ) : (
          <ul className="border-t border-[#1a1a1a]">
            {tasksFromSessions.map((t) => (
              <li
                key={t.name}
                className="flex items-center justify-between gap-3 border-b border-[#1a1a1a] py-2 last:border-b-0"
              >
                <span className="max-w-[65%] truncate font-mono text-[12px] text-[#d4d4d4]">{t.name}</span>
                <span className="shrink-0 font-mono text-[12px] text-[#4b4b4b]">{fmtMin(t.minutes)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {meetingsFromEod.length > 0 && (
        <div className="mt-3">
          <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
            Meetings
          </h3>
          <ul className="space-y-1">
            {meetingsFromEod.map((m, idx) => (
              <li key={`${m.name}-${idx}`} className="flex justify-between gap-2">
                <span className="min-w-0 truncate font-mono text-[12px] text-[#6b6b6b]">{m.name}</span>
                <span className="shrink-0 font-mono text-[12px] text-[#4b4b4b]">{Math.round(m.minutes)}m</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-3">
        <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Performance
        </h3>
        {(() => {
          const tasksWorked = Number(perf?.tasks_worked_today ?? 0) || 0
          const tasksCompleted = Number(perf?.tasks_completed_today ?? 0) || 0
          const tasksOverdue = Number(perf?.tasks_overdue ?? 0) || 0
          const within = Number(perf?.tasks_within_estimate ?? 0) || 0
          const over = Number(perf?.tasks_over_estimate ?? 0) || 0
          const overdueNames = Array.isArray(perf?.overdue_task_names)
            ? (perf.overdue_task_names as any[]).map((x) => String(x)).filter(Boolean)
            : []

          const workedNames =
            tasksFromEod && tasksFromEod.length > 0
              ? tasksFromEod.map((t) => t.name).filter(Boolean)
              : tasksFromSessions.map((t) => t.name).filter(Boolean)

          const completedNames =
            tasksFromEod && tasksFromEod.length > 0
              ? tasksFromEod
                  .filter((t) => {
                    const s = String(t.status || '').toLowerCase()
                    return s === 'closed' || s === 'complete' || s === 'completed' || s === 'done'
                  })
                  .map((t) => t.name)
                  .filter(Boolean)
              : []

          const withinNames =
            tasksFromEod && tasksFromEod.length > 0
              ? tasksFromEod
                  .filter((t) => t.percent_of_estimate != null && Number(t.percent_of_estimate) <= 100)
                  .map((t) => t.name)
                  .filter(Boolean)
              : []

          const overNames =
            tasksFromEod && tasksFromEod.length > 0
              ? tasksFromEod
                  .filter((t) => t.percent_of_estimate != null && Number(t.percent_of_estimate) > 100)
                  .map((t) => t.name)
                  .filter(Boolean)
              : []

          const Row = ({
            k,
            v,
            valueClass,
          }: {
            k: string
            v: string
            valueClass?: string
          }) => (
            <div className="flex gap-4">
              <span className="w-40 font-mono text-[11px] text-[#4b4b4b]">{k}</span>
              <span className={`break-words font-mono text-[11px] ${valueClass ?? 'text-[#d4d4d4]'}`}>{v}</span>
            </div>
          )

          return (
            <div className="space-y-1">
              <Row k="Tasks worked" v={workedNames.length > 0 ? workedNames.join(', ') : String(tasksWorked)} />
              <Row
                k="Tasks completed"
                v={completedNames.length > 0 ? completedNames.join(', ') : tasksCompleted > 0 ? String(tasksCompleted) : '—'}
              />
              <Row
                k="Overdue"
                v={tasksOverdue > 0 ? (overdueNames.length > 0 ? overdueNames.join(', ') : '—') : '—'}
                valueClass={tasksOverdue > 0 ? 'text-amber-600' : 'text-[#d4d4d4]'}
              />
              <Row k="Within estimate" v={withinNames.length > 0 ? withinNames.join(', ') : within > 0 ? String(within) : '—'} />
              <Row k="Over estimate" v={overNames.length > 0 ? overNames.join(', ') : over > 0 ? String(over) : '—'} />
            </div>
          )
        })()}
      </div>

      <div className="mt-3">
        <h3 className="font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Skill profile
        </h3>
        {skillData?.period ? (
          <p className="mt-1 font-mono text-[10px] text-[#3a3a3a]">{String(skillData.period)}</p>
        ) : null}

        {!skillData && (
          <p className="mt-2 font-mono text-[11px] text-[#3a3a3a]">No skill data yet — runs Monday 9am</p>
        )}

        {skillData && (
          <>
            <div className="mt-2">
              <p className="font-mono text-[12px] text-[#6ee7b7]">Top skill: {String(skillData.top_skill ?? '—')}</p>
              <p className="mt-0.5 font-mono text-[11px] text-[#4b4b4b]">
                Focus: {Number(skillData.focus_score ?? 0).toFixed(1)}%
              </p>
              <p className="mt-0.5 font-mono text-[11px] text-[#4b4b4b]">
                Consistency: {String(skillData.consistency ?? '—')}
              </p>
            </div>

            <button
              type="button"
              onClick={() => setShowPayload((v) => !v)}
              className="mt-2 font-mono text-[10px] text-[#4b4b4b] hover:text-[#6b6b6b]"
            >
              {showPayload ? 'hide breakdown' : 'show breakdown'}
            </button>

            {showPayload && (
              <div className="mt-3 space-y-2">
                {(() => {
                  const sb = skillData?.skill_breakdown
                  if (!sb || typeof sb !== 'object') return null
                  const top = String(skillData?.top_skill ?? '').trim().toLowerCase()
                  const rows = Object.entries(sb as Record<string, any>)
                    .map(([k, v]) => {
                      const minutes = Number(v?.minutes ?? 0) || 0
                      const pct = Number(v?.percentage ?? 0) || 0
                      const tasks = Array.isArray(v?.tasks) ? (v.tasks as any[]).map((x) => String(x)) : []
                      return { key: k, minutes, pct, tasks }
                    })
                    .filter((r) => r.minutes > 0 || r.pct > 0)
                    .sort((a, b) => b.pct - a.pct)

                  const barColor = (skillKey: string) => {
                    const sk = String(skillKey).toLowerCase()
                    if (sk === top) return 'bg-[#1d9e75]'
                    if (sk === 'meeting') return 'bg-[#534AB7]'
                    return 'bg-[#4b4b4b]'
                  }

                  return rows.map((r) => {
                    const shown = r.tasks.slice(0, 3)
                    const more = Math.max(0, r.tasks.length - shown.length)
                    return (
                      <div key={r.key} className="pt-1">
                        <p className="font-mono text-[11px] text-[#d4d4d4]">
                          {skillLabel(r.key)} · {r.pct.toFixed(1)}% · {fmtMin(r.minutes)}
                        </p>
                        <div className="mt-0.5 h-1 w-full rounded bg-[#1e1e1e]">
                          <div
                            className={`h-1 rounded ${barColor(r.key)}`}
                            style={{ width: `${Math.max(0, Math.min(100, r.pct))}%` }}
                          />
                        </div>
                        {shown.length > 0 && (
                          <div className="mt-1 space-y-0.5">
                            {shown.map((t) => (
                              <p key={t} className="font-mono text-[10px] text-[#3a3a3a]">
                                · {t}
                              </p>
                            ))}
                            {more > 0 && (
                              <p className="font-mono text-[10px] text-[#3a3a3a]">+ {more} more</p>
                            )}
                          </div>
                        )}
                      </div>
                    )
                  })
                })()}
              </div>
            )}

            <p className="mt-3 font-mono text-[10px] text-[#3a3a3a]">
              {String(skillData.period ?? '—')} · generated {generatedAtLabel}
            </p>
          </>
        )}
      </div>
    </div>
  )
}

