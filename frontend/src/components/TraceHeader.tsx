interface TraceHeaderProps {
  date: string
  displayDate: string
  onPrev: () => void
  onNext: () => void
  onDateChange: (iso: string) => void
  user: string
  awLive: boolean
}

export function TraceHeader({
  date,
  displayDate,
  onPrev,
  onNext,
  onDateChange,
  user,
  awLive,
}: TraceHeaderProps) {
  return (
    <header
      className="fixed left-0 right-0 top-0 z-50 flex h-[44px] items-center justify-between border-b border-[#1e1e1e] bg-[#0c0c0c] px-4"
      style={{ backgroundColor: '#0c0c0c' }}
    >
      <div className="flex min-w-0 items-center gap-3">
        <span className="font-mono text-[15px] font-medium tracking-[0.18em] text-[#e8e8e8]">
          TRACE
        </span>
        <span className="flex items-center gap-1.5 font-mono text-[12px] text-[#4b4b4b]">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-[#1d9e75]" aria-hidden />
          live · auto 30s
        </span>
      </div>

      <div className="flex min-w-0 max-w-[70%] items-center justify-end gap-2 sm:gap-3">
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={onPrev}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-[#1e1e1e] font-mono text-xs text-[#6b6b6b] hover:bg-[#0f0f0f]"
            aria-label="Previous day"
          >
            ←
          </button>
          <label className="relative min-w-[6.5rem] cursor-pointer text-center font-mono text-[14px] text-[#d4d4d4]">
            <input
              type="date"
              value={date}
              onChange={(e) => onDateChange(e.target.value)}
              className="absolute inset-0 cursor-pointer opacity-0"
              aria-label="Pick date"
            />
            {displayDate}
          </label>
          <button
            type="button"
            onClick={onNext}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded border border-[#1e1e1e] font-mono text-xs text-[#6b6b6b] hover:bg-[#0f0f0f]"
            aria-label="Next day"
          >
            →
          </button>
        </div>
        <span className="max-w-[100px] truncate font-mono text-[12px] text-[#6b6b6b] sm:max-w-[200px]" title={user}>
          {user || '—'}
        </span>
        <span
          className={`h-2 w-2 shrink-0 rounded-full ${awLive ? 'bg-[#1d9e75]' : 'bg-red-600'}`}
          title={awLive ? 'AW live' : 'AW offline'}
          aria-hidden
        />
      </div>
    </header>
  )
}
