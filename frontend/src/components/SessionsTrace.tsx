import { useState } from 'react'
import type { AppBreakdownBlock, TraceSession } from '../lib/trace'
import { formatTimeRangeIST } from '../lib/istFormat'
import { clickupTaskUrl } from '../lib/clickupTaskUrl'
import { buildSessionAppBreakdown, type AppDotKind } from '../lib/sessionAppBreakdown'
import { domainChipsFromUrls } from '../lib/sessionDomains'

const INITIAL_VISIBLE = 4

const BREAKDOWN_DOT = {
  browser: '#60a5fa',
  editor: '#6ee7b7',
  other: '#6b6b6b',
} as const

function breakdownHeaderDot(b: AppBreakdownBlock): string {
  if (b.is_browser) return BREAKDOWN_DOT.browser
  if (b.is_editor) return BREAKDOWN_DOT.editor
  return BREAKDOWN_DOT.other
}

function legacyBreakdownDot(kind: AppDotKind): string {
  if (kind === 'browser') return BREAKDOWN_DOT.browser
  if (kind === 'editor') return BREAKDOWN_DOT.editor
  return BREAKDOWN_DOT.other
}

function truncateUrl(url: string, maxLen = 60): string {
  if (url.length <= maxLen) return url
  return `${url.slice(0, maxLen - 1)}…`
}

function zoneBorderClass(z: string): string {
  switch (z) {
    case 'task_linked':
      return 'border-l-[#1d9e75]'
    case 'meeting':
      return 'border-l-[#378add]'
    case 'untracked_work':
    case 'untracked':
      return 'border-l-[#ba7517]'
    default:
      return 'border-l-[#3a3a3a]'
  }
}

function zoneBadge(z: string): { label: string; className: string } {
  switch (z) {
    case 'meeting':
      return {
        label: 'MEETING',
        className: 'bg-[#0c2340] text-[#378add]',
      }
    case 'task_linked':
      return {
        label: 'TASK',
        className: 'bg-[#0a2318] text-[#1d9e75]',
      }
    case 'untracked_work':
    case 'untracked':
      return {
        label: 'UNTRACKED',
        className: 'bg-[#2a1800] text-[#ba7517]',
      }
    default:
      return {
        label: 'UNKNOWN',
        className: 'bg-[#1a1a1a] text-[#4b4b4b]',
      }
  }
}

function confDotClass(c: number): string {
  if (c >= 0.85) return 'bg-[#1d9e75]'
  if (c >= 0.65) return 'bg-amber-500'
  return 'bg-red-600'
}

function parseTaskLines(name: string | null): { main: string; sub?: string } {
  if (!name) return { main: '—' }
  const parts = name.split(/\s[—–]\s/)
  if (parts.length >= 2) {
    return { main: parts[0].trim(), sub: parts.slice(1).join(' — ').trim() }
  }
  return { main: name }
}

interface SessionsTraceProps {
  sessions: TraceSession[]
}

export function SessionsTrace({ sessions }: SessionsTraceProps) {
  const [openId, setOpenId] = useState<string | null>(null)
  const [showAll, setShowAll] = useState(false)

  const toggle = (id: string) => {
    setOpenId((cur) => (cur === id ? null : id))
  }

  const hiddenCount = sessions.length > INITIAL_VISIBLE ? sessions.length - INITIAL_VISIBLE : 0
  const visibleSessions = showAll || hiddenCount === 0 ? sessions : sessions.slice(0, INITIAL_VISIBLE)

  return (
    <section className="min-h-0 flex-1 overflow-y-auto pb-8 [scrollbar-width:none] [-ms-overflow-style:none] [&::-webkit-scrollbar]:hidden">
      <h2 className="mb-3 font-sans text-[11px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
        Sessions — {sessions.length} today
      </h2>
      <ul className="space-y-2">
        {visibleSessions.map((s, idx) => {
          const rowKey = s.session_id ?? `row-${idx}-${s.start}-${s.end}`
          const id = rowKey
          const open = openId === id
          const badge = zoneBadge(String(s.zone))
          const { main: taskMain, sub: taskSub } = parseTaskLines(s.clickup_task_name)
          const legacyBlocks = buildSessionAppBreakdown(s)
          const pipelineBlocks = s.app_breakdown?.length ? s.app_breakdown : null
          const dur = Math.round(Number(s.duration_min) || 0)
          const chips = domainChipsFromUrls(s.urls || [])
          const input = s.input ?? {
            keystrokes: 0,
            mouse_clicks: 0,
            activity_rate: 0,
          }
          const act = Number(input.activity_rate) || 0
          const showActivityPct = act > 0

          return (
            <li
              key={rowKey}
              className="overflow-hidden rounded-md border border-[#1e1e1e] bg-[#0c0c0c]"
            >
              <div
                role="button"
                tabIndex={0}
                aria-expanded={open}
                onClick={() => toggle(id)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    toggle(id)
                  }
                }}
                className={`cursor-pointer border-l-2 px-3 py-2.5 text-left transition-colors duration-200 outline-none hover:bg-[#0f0f0f] focus-visible:ring-1 focus-visible:ring-[#378add] ${zoneBorderClass(String(s.zone))}`}
              >
                <div className="flex flex-col gap-2">
                  <div className="flex items-start gap-2 sm:items-center sm:gap-3">
                    <span className="shrink-0 pt-0.5 font-mono text-[14px] text-[#6b6b6b] sm:pt-0">
                      {formatTimeRangeIST(s.start, s.end)}
                    </span>
                    <span
                      className={`shrink-0 rounded px-2 py-0.5 font-sans text-[11px] font-semibold uppercase tracking-wide ${badge.className}`}
                    >
                      {badge.label}
                    </span>
                    <div className="min-w-0 flex-1">
                      {taskSub && String(s.zone) === 'task_linked' ? (
                        <>
                          <span
                            className="block leading-snug"
                            style={{ fontSize: '14px', color: '#d4d4d4', fontWeight: 500 }}
                          >
                            {taskSub}
                          </span>
                          <span className="mt-0.5 block" style={{ fontSize: '11px', color: '#4b4b4b' }}>
                            <span style={{ fontStyle: 'italic' }}>via </span>{taskMain}
                          </span>
                        </>
                      ) : (
                        <span className="block text-[15px] font-medium leading-snug text-[#d4d4d4]">
                          {taskMain}
                        </span>
                      )}
                    </div>
                    <div className="flex shrink-0 items-center gap-2 self-start sm:self-center">
                      <span className="font-mono text-[12px] text-[#4b4b4b]">{dur}m</span>
                      <span
                        className={`h-2 w-2 rounded-full ${confDotClass(Number(s.map_confidence) || 0)}`}
                        title={`confidence ${s.map_confidence}`}
                      />
                      <span
                        className={`inline-block text-[#6b6b6b] transition-transform duration-200 ease-out ${open ? 'rotate-180' : 'rotate-0'}`}
                        aria-hidden
                      >
                        ↓
                      </span>
                    </div>
                  </div>
                  <p className="font-mono text-[11px] leading-relaxed text-[#4b4b4b]">
                    source: {s.map_method} · conf {Number(s.map_confidence).toFixed(2)}
                    {showActivityPct && (
                      <>
                        {' '}
                        · {act.toFixed(1)}% active
                      </>
                    )}
                  </p>
                  {chips.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {chips.map((d) => (
                        <span
                          key={d}
                          className="rounded border border-[#2a2a2a] bg-[#111111] px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide text-[#6b6b6b]"
                        >
                          {d}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
              {open && (
                <div className="border-t border-[#1e1e1e] bg-[#111111] px-3 py-3 sm:px-4">
                  <p className="font-mono text-[11px] text-[#4b4b4b]">
                    {input.keystrokes} keystrokes · {input.mouse_clicks} clicks
                    {showActivityPct && (
                      <>
                        {' '}
                        · {act.toFixed(1)}% active
                      </>
                    )}
                  </p>

                  <div className="mt-3">
                    <h3 className="mb-2 font-sans text-[11px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
                      Browser &amp; app breakdown
                    </h3>
                    <div className="space-y-5">
                      {pipelineBlocks &&
                        pipelineBlocks.map((b) => (
                          <div key={b.app}>
                            <div className="mb-2 flex w-full items-center justify-between gap-2">
                              <div className="flex min-w-0 items-center gap-2 font-mono text-[11px] uppercase tracking-wide text-[#6b6b6b]">
                                <span
                                  className="h-2 w-2 shrink-0 rounded-full"
                                  style={{ backgroundColor: breakdownHeaderDot(b) }}
                                />
                                <span className="truncate">{b.app}</span>
                              </div>
                              <span className="shrink-0 font-mono text-[11px] text-[#4b4b4b]">
                                {b.total_minutes}m
                              </span>
                            </div>
                            <ul className="space-y-0 border-t border-[#1a1a1a]">
                              {b.tabs.map((tab, ti) => (
                                <li
                                  key={`${tab.title}-${ti}`}
                                  className="border-b-[0.5px] border-[#1a1a1a] py-2.5 last:border-b-0"
                                >
                                  <div className="flex items-start gap-3">
                                    <div className="min-w-0 flex-1">
                                      <p className="break-words text-[13px] text-[#d4d4d4]">{tab.title}</p>
                                      {tab.url ? (
                                        <p className="mt-1 font-mono text-[11px] leading-relaxed text-[#4b4b4b]">
                                          {truncateUrl(String(tab.url))}
                                        </p>
                                      ) : null}
                                      {b.is_editor && tab.project ? (
                                        <p className="mt-0.5 font-sans text-[11px] text-[#4b4b4b]">
                                          in {tab.project}
                                        </p>
                                      ) : null}
                                    </div>
                                    <span className="shrink-0 text-right font-mono text-[11px] text-[#4b4b4b]">
                                      {tab.minutes}m
                                    </span>
                                  </div>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ))}

                      {!pipelineBlocks &&
                        legacyBlocks.map((b) => (
                          <div key={b.app}>
                            <div className="mb-2 flex w-full items-center justify-between gap-2">
                              <div className="flex min-w-0 items-center gap-2 font-mono text-[11px] uppercase tracking-wide text-[#6b6b6b]">
                                <span
                                  className="h-2 w-2 shrink-0 rounded-full"
                                  style={{ backgroundColor: legacyBreakdownDot(b.dotKind) }}
                                />
                                <span className="truncate">{b.app}</span>
                              </div>
                              <span className="shrink-0 font-mono text-[11px] text-[#4b4b4b]">
                                {Math.round(b.totalMinutes)}m
                              </span>
                            </div>
                            <ul className="space-y-0 border-t border-[#1a1a1a]">
                              {b.entries.map((e, i) => (
                                <li
                                  key={`${e.title}-${i}`}
                                  className="border-b-[0.5px] border-[#1a1a1a] py-2.5 last:border-b-0"
                                >
                                  <div className="flex items-start gap-3">
                                    <div className="min-w-0 flex-1">
                                      <p className="break-words text-[13px] text-[#d4d4d4]">{e.title}</p>
                                      {e.url && (
                                        <p className="mt-1 font-mono text-[11px] leading-relaxed text-[#4b4b4b]">
                                          {truncateUrl(e.url)}
                                        </p>
                                      )}
                                      {b.dotKind === 'editor' && e.subtitle && (
                                        <p className="mt-0.5 font-sans text-[11px] text-[#4b4b4b]">
                                          in {e.subtitle}
                                        </p>
                                      )}
                                    </div>
                                    <span className="shrink-0 text-right font-mono text-[11px] text-[#4b4b4b]">
                                      {Math.round(e.minutes)}m
                                    </span>
                                  </div>
                                </li>
                              ))}
                            </ul>
                          </div>
                        ))}

                      {!pipelineBlocks && legacyBlocks.length === 0 && (
                        <p className="font-mono text-[12px] text-[#4b4b4b]">
                          No window breakdown (re-run pipeline with latest code).
                        </p>
                      )}
                    </div>
                  </div>

                  <p className="mt-4 font-mono text-[11px] text-[#4b4b4b]">
                    {input.keystrokes} keystrokes · {input.mouse_clicks} clicks
                    {showActivityPct && (
                      <>
                        {' '}
                        · {act.toFixed(1)}% active
                      </>
                    )}
                    {' '}
                    · matched:{' '}
                    {s.clickup_task_id ? (
                      <a
                        href={clickupTaskUrl(s.clickup_task_id) ?? '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[#6ee7b7] underline-offset-2 hover:underline"
                      >
                        {s.clickup_task_name || s.clickup_task_id}
                      </a>
                    ) : (
                      <span>{s.clickup_task_name || '—'}</span>
                    )}
                  </p>
                </div>
              )}
            </li>
          )
        })}
      </ul>
      {hiddenCount > 0 && !showAll && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="mt-3 font-mono text-[12px] text-[#6ee7b7] underline-offset-2 hover:underline"
        >
          + {hiddenCount} more sessions
        </button>
      )}
      {showAll && hiddenCount > 0 && (
        <button
          type="button"
          onClick={() => setShowAll(false)}
          className="mt-3 font-mono text-[12px] text-[#4b4b4b] underline-offset-2 hover:text-[#6b6b6b] hover:underline"
        >
          Show fewer
        </button>
      )}
    </section>
  )
}
