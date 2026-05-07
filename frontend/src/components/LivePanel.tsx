import { useEffect, useState } from 'react'
import type { InputLiveState, WindowLiveState } from '../lib/useActivityWatch'
import type { DailyJsonTrace, TraceSession } from '../lib/trace'
import { formatClock12AmpmIST, parseIsoToDate, todayISOInIST } from '../lib/istFormat'

type ClickUpTouchedTask = {
  id: string
  name: string
  status: string
  parent: string | null
  list?: string
  url?: string
}

function normStatus(s: string): string {
  return (s || '').trim().toLowerCase()
}

function statusKind(s: string): 'in_progress' | 'open' | 'complete' {
  const x = normStatus(s)
  if (x.includes('progress')) return 'in_progress'
  if (x === 'complete' || x === 'completed' || x === 'closed' || x === 'done') return 'complete'
  return 'open'
}

function statusIsClosed(s: string): boolean {
  return statusKind(s) === 'complete'
}

function dotForStatusKind(k: 'in_progress' | 'open' | 'complete'): { kind: 'dot' | 'check'; color: string } {
  if (k === 'in_progress') return { kind: 'dot', color: '#1d9e75' }
  if (k === 'complete') return { kind: 'check', color: '#6ee7b7' }
  return { kind: 'dot', color: '#6b6b6b' }
}

function guessNowTaskName(windowTitle: string, tasks: ClickUpTouchedTask[]): ClickUpTouchedTask | null {
  const t = (windowTitle || '').trim()
  if (!t) return null
  const tl = t.toLowerCase()

  // Strong match: task name appears in window title, or vice versa
  for (const task of tasks) {
    const n = (task.name || '').trim()
    if (!n) continue
    const nl = n.toLowerCase()
    if (nl.length >= 6 && tl.includes(nl)) return task
    if (tl.length >= 6 && nl.includes(tl)) return task
  }

  return null
}

function isLocalhostDomain(d: string): boolean {
  const x = d.toLowerCase()
  return (
    x.includes('localhost') ||
    x.includes('127.0.0.1') ||
    x.includes('::1') ||
    x === 'localhost'
  )
}

// ClickUp assignee tasks from proxy (all non-closed, paginated; optional status filter in .env).

async function fetchClickUpAssigneeTasks(signal?: AbortSignal): Promise<ClickUpTouchedTask[]> {
  const r = await fetch('http://localhost:5899/clickup/tasks', { signal })
  const raw = await r.json().catch(() => null)
  if (!r.ok) return []
  if (!Array.isArray(raw)) return []
  return raw
    .filter((x) => x && typeof x === 'object')
    .map((x) => ({
      id: String((x as any).id ?? ''),
      name: String((x as any).name ?? ''),
      status: String((x as any).status ?? ''),
      parent: (x as any).parent != null ? String((x as any).parent) : null,
      list: String((x as any).list ?? ''),
      url: String((x as any).url ?? ''),
    }))
    .filter((t) => t.id && t.name)
}

function schedulerNextFrom(generatedAt?: string): string {
  if (!generatedAt) return '—'
  const d = parseIsoToDate(generatedAt)
  if (Number.isNaN(d.getTime())) return '—'
  const next = new Date(d.getTime() + 30 * 60 * 1000)
  return formatClock12AmpmIST(next)
}

interface LivePanelProps {
  daily: DailyJsonTrace | null
  sessions: TraceSession[]
  windowState: WindowLiveState
  inputState: InputLiveState
  elapsedSec: number
  awLive: boolean
}

export function LivePanel({
  daily,
  sessions: _sessions,
  windowState,
  inputState,
  elapsedSec,
  awLive,
}: LivePanelProps) {
  const isToday = daily?.date === todayISOInIST()

  const domains = (daily?.web_domain_summary?.domains_top ?? [])
    .filter((d) => d.domain && !isLocalhostDomain(d.domain))
    .slice(0, 8)

  const [touchedTasks, setTouchedTasks] = useState<ClickUpTouchedTask[]>([])

  useEffect(() => {
    const ac = new AbortController()
    const run = async () => {
      const tasks = await fetchClickUpAssigneeTasks(ac.signal)
      // Debug: verify parent IDs exist on subtasks for 3-level tree rendering.
      // eslint-disable-next-line no-console
      console.log('[clickup/tasks] sample', tasks.slice(0, 8))
      setTouchedTasks(tasks)
    }
    run()
    const t = window.setInterval(run, 2 * 60 * 1000)
    return () => {
      ac.abort()
      window.clearInterval(t)
    }
  }, [])

  const tasksById = new Map<string, ClickUpTouchedTask>()
  for (const t of touchedTasks) tasksById.set(t.id, t)

  const childrenByParent = new Map<string, ClickUpTouchedTask[]>()
  for (const t of touchedTasks) {
    if (!t.parent) continue
    if (!childrenByParent.has(t.parent)) childrenByParent.set(t.parent, [])
    childrenByParent.get(t.parent)!.push(t)
  }

  const grouped = {
    in_progress: [] as ClickUpTouchedTask[],
    open: [] as ClickUpTouchedTask[],
    complete: [] as ClickUpTouchedTask[],
  }

  // Group only root tasks, but DO NOT rely on the proxy/API to status-filter.
  // Subtasks often carry different statuses than their parents.
  for (const t of touchedTasks) {
    if (t.parent) continue
    grouped[statusKind(t.status)].push(t)
  }

  for (const k of Object.keys(grouped) as (keyof typeof grouped)[]) {
    grouped[k].sort((a, b) => a.name.localeCompare(b.name))
  }

  const lastRun =
    daily?.generated_at != null ? formatClock12AmpmIST(parseIsoToDate(daily.generated_at)) : '—'
  const nextRun = schedulerNextFrom(daily?.generated_at)

  const xm = Math.floor(elapsedSec / 60)
  const xs = elapsedSec % 60
  const inWindowStr = xm > 0 ? `${xm}m ${xs}s in window` : `${xs}s in window`

  return (
    <aside className="flex min-h-0 w-full flex-col gap-4 overflow-y-auto pb-8 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden lg:max-w-[38%] lg:flex-[0_0_38%]">
      <div className="rounded-md border border-[#1e1e1e] bg-transparent p-3">
        <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Now
        </h3>
        {!isToday && (
          <p className="font-mono text-[11px] text-[#3a3a3a]">Live window when viewing today.</p>
        )}
        <div className={!isToday ? 'opacity-50' : ''}>
          <p className="text-[17px] font-medium text-[#e8e8e8]">{windowState.app}</p>
          <p className="mt-1 break-words font-mono text-[12px] text-[#6b6b6b]">{windowState.title}</p>
          <p className="mt-2 font-mono text-[12px] text-[#6ee7b7]">{inWindowStr}</p>
        </div>
      </div>

      <div className="rounded-md border border-[#1e1e1e] bg-transparent p-3">
        <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Input today
        </h3>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <p className="font-mono text-xl text-[#e8e8e8]">
              {inputState.keystrokes.toLocaleString()}
            </p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">
              Keystrokes
            </p>
          </div>
          <div>
            <p className="font-mono text-xl text-[#e8e8e8]">
              {inputState.mouseClicks.toLocaleString()}
            </p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">Clicks</p>
          </div>
          <div>
            <p className="font-mono text-xl text-[#e8e8e8]">
              {inputState.scrollUnits.toLocaleString()}
            </p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">Scroll</p>
          </div>
          <div>
            <p className="font-mono text-xl text-[#6ee7b7]">
              {(daily?.totals.productivity_pct ?? daily?.totals.activity ?? 0).toFixed(1)}%
            </p>
            <p className="mt-1 font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">
              Activity %
            </p>
          </div>
        </div>
        {!awLive && (
          <p className="mt-2 font-mono text-[10px] text-amber-700">
            AW live data incomplete (proxy :5899, ActivityWatch :5600, optional aw-watcher-input)
          </p>
        )}
      </div>

      <div className="rounded-md border border-[#1e1e1e] bg-transparent p-3">
        <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          In progress
        </h3>
        {(() => {
          const g = guessNowTaskName(windowState.title, touchedTasks)
          if (!g) return null
          return (
            <p className="mb-2 font-mono text-[11px] text-[#4b4b4b]">
              now: <span className="text-[#d4d4d4]">{g.name}</span>
            </p>
          )
        })()}
        {touchedTasks.length === 0 ? (
          <p className="font-mono text-[11px] text-[#3a3a3a]">No open assignee tasks (check proxy / token / CLICKUP_ASSIGNEE_ID)</p>
        ) : (
          <div className="space-y-3">
            {(['in_progress', 'open', 'complete'] as const).map((k) => {
              const tasks = grouped[k]
              if (tasks.length === 0) return null
              const dot = dotForStatusKind(k)
              const title = k === 'in_progress' ? 'in progress' : k === 'complete' ? 'complete' : 'open'
              return (
                <div key={k}>
                  <div className="mb-1 flex items-center gap-2">
                    {dot.kind === 'dot' ? (
                      <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: dot.color }} />
                    ) : (
                      <span className="text-[12px]" style={{ color: dot.color }}>
                        ✓
                      </span>
                    )}
                    <span className="font-sans text-[11px] uppercase tracking-wide text-[#4b4b4b]">
                      {title}
                    </span>
                  </div>
                  <ul className="space-y-0.5">
                    {tasks.map((t) => {
                      // Always show subtree for the root task, regardless of child status.
                      // (Child statuses may differ from parent; we display them as nested items.)
                      const level2 = (childrenByParent.get(t.id) ?? [])
                        .slice()
                        .sort((a, b) => a.name.localeCompare(b.name))

                      return (
                        <li key={t.id} className={k === 'complete' ? 'opacity-60' : ''}>
                          {/* Level 1: 13px #d4d4d4 bold */}
                          {t.url ? (
                            <a
                              href={t.url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-bold underline-offset-2 hover:underline"
                              style={{ fontSize: '13px', color: '#d4d4d4' }}
                            >
                              {t.name}
                            </a>
                          ) : (
                            <span className="font-bold" style={{ fontSize: '13px', color: '#d4d4d4' }}>
                              {t.name}
                            </span>
                          )}
                          {level2.length > 0 && (
                            <ul className="mt-0.5 space-y-0.5">
                              {level2.map((c) => {
                                const level3 = (childrenByParent.get(c.id) ?? [])
                                  .slice()
                                  .sort((a, b) => a.name.localeCompare(b.name))

                                return (
                                  <li key={c.id} style={{ paddingLeft: '12px' }}>
                                    {/* Level 2: 12px #9ca3af normal, indented 12px */}
                                    <span className="font-mono" style={{ fontSize: '12px', color: '#9ca3af' }}>
                                      └{' '}
                                      {c.url ? (
                                        <a
                                          href={c.url}
                                          target="_blank"
                                          rel="noreferrer"
                                          className="underline-offset-2 hover:underline"
                                        >
                                          {c.name}
                                        </a>
                                      ) : (
                                        c.name
                                      )}
                                    </span>
                                    {level3.length > 0 && (
                                      <ul className="mt-0.5 space-y-0.5">
                                        {level3.map((gc) => (
                                          <li key={gc.id} style={{ paddingLeft: '12px' }}>
                                            {/* Level 3: 11px #6b6b6b normal, indented 24px total (12+12) */}
                                            <span className="font-mono" style={{ fontSize: '11px', color: '#6b6b6b' }}>
                                              └{' '}
                                              {gc.url ? (
                                                <a
                                                  href={gc.url}
                                                  target="_blank"
                                                  rel="noreferrer"
                                                  className="underline-offset-2 hover:underline"
                                                >
                                                  {gc.name}
                                                </a>
                                              ) : (
                                                gc.name
                                              )}
                                            </span>
                                          </li>
                                        ))}
                                      </ul>
                                    )}
                                  </li>
                                )
                              })}
                            </ul>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="rounded-md border border-[#1e1e1e] bg-transparent p-3">
        <h3 className="mb-2 font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Top domains
        </h3>
        <ul className="space-y-1.5">
          {domains.map((d) => (
            <li key={d.domain} className="flex justify-between gap-2 font-mono text-[12px]">
              <span className="min-w-0 break-all text-[#6b6b6b]">{d.domain}</span>
              <span className="shrink-0 text-[#4b4b4b]">{Math.round(d.minutes)}m</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="rounded-md border border-[#1e1e1e] bg-transparent p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-[#1d9e75]" />
            <span className="font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
              Scheduler running
            </span>
          </div>
          <p className="font-mono text-[12px] text-[#3a3a3a]">
            next: <span className="text-[#4b4b4b]">{nextRun}</span>
          </p>
        </div>
        <p className="mt-2 font-mono text-[12px] text-[#3a3a3a]">last run: {lastRun}</p>
      </div>
    </aside>
  )
}
