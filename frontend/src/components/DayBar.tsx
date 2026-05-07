import type { DailyTotalsTrace } from '../lib/trace'

function fmtHmLower(mins: number): string {
  const m = Math.round(mins)
  const h = Math.floor(m / 60)
  const r = m % 60
  if (h <= 0) return `${r}m`
  if (r === 0) return `${h}h`
  return `${h}h ${r}m`
}

interface DayBarProps {
  totals: DailyTotalsTrace
}

export function DayBar({ totals }: DayBarProps) {
  const active = Number(totals.active_minutes) || 0
  const idleRaw = Number(totals.idle_minutes) || 0
  const task = Number(totals.task_linked_minutes) || 0
  const meet = Number(totals.meeting_minutes) || 0
  const unknown = (Number(totals.unknown_minutes) || 0) + (Number(totals.unclear_minutes) || 0)
  const idle = idleRaw + unknown

  const summaryLine = `active ${fmtHmLower(active)} · deep work ${fmtHmLower(task)} · meetings ${fmtHmLower(meet)} · idle ${fmtHmLower(idle)}`

  return (
    <div className="border-b border-[#1e1e1e] bg-[#0c0c0c] px-4 py-4">
      <div className="mb-0 flex flex-wrap items-start justify-between gap-2">
        <h2 className="font-sans text-[12px] font-normal uppercase tracking-[0.12em] text-[#4b4b4b]">
          Day overview
        </h2>
      </div>
      <p
        className="font-mono text-[12px] lowercase leading-relaxed"
        style={{
          color: 'var(--color-text-secondary, #6b6b6b)',
          padding: '4px 0 8px 0',
        }}
      >
        {summaryLine}
      </p>
    </div>
  )
}
