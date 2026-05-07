import { useMemo, useState } from 'react'
import { DayBar } from './components/DayBar'
import { DayTimeline } from './components/DayTimeline'
import { EmployeeCard } from './components/EmployeeCard'
import { LivePanel } from './components/LivePanel'
import { SegmentsTrace } from './components/SegmentsTrace'
import { TraceHeader } from './components/TraceHeader'
import { useActivityWatch } from './lib/useActivityWatch'
import { useTraceData } from './lib/useTraceData'
import { addDaysISO, formatDateHeaderIST, todayISOInIST } from './lib/istFormat'

export default function App() {
  const [date, setDate] = useState(todayISOInIST())
  const { daily, sessions, error, loading } = useTraceData(date)
  const { windowState, inputState, elapsedSec, awLive } = useActivityWatch(true)

  const displayDate = useMemo(() => formatDateHeaderIST(date), [date])
  const user = daily?.user ?? '—'

  return (
    <div className="min-h-screen bg-[#0c0c0c] text-[#d4d4d4]">
      <TraceHeader
        date={date}
        displayDate={displayDate}
        onPrev={() => setDate((d) => addDaysISO(d, -1))}
        onNext={() => setDate((d) => addDaysISO(d, 1))}
        onDateChange={setDate}
        user={user}
        awLive={awLive}
      />

      <main className="pt-[44px]">
        {loading && (
          <div className="px-4 py-6 font-mono text-[13px] text-[#4b4b4b]">Loading…</div>
        )}

        {!loading && error === 'not_found' && !daily && (
          <div className="flex flex-col gap-4 px-4 py-6 lg:flex-row lg:items-start">
            <div className="flex min-h-[40vh] flex-[0_0_62%] flex-col items-center justify-center">
              <p className="text-sm text-[#3a3a3a]">No data for this date</p>
              <p className="mt-2 text-center font-mono text-xs text-[#2a2a2a]">
                {`cd backend && python main.py --date ${date} --write-out`}
              </p>
            </div>
            <LivePanel
              daily={null}
              sessions={[]}
              windowState={windowState}
              inputState={inputState}
              elapsedSec={elapsedSec}
              awLive={awLive}
            />
          </div>
        )}

        {!loading && daily && (
          <>
            <DayBar totals={daily.totals} />
            <div className="flex justify-center px-4 pt-4">
              <div className="w-full max-w-2xl">
                <EmployeeCard daily={daily} sessions={sessions} />
              </div>
            </div>
            <div className="flex flex-col gap-4 px-4 py-4 lg:flex-row lg:items-start">
              <div className="min-h-0 w-full min-w-0 lg:w-[62%] lg:flex-[0_0_62%]">
                <DayTimeline date={date} />
                <SegmentsTrace date={date} />
              </div>
              <LivePanel
                daily={daily}
                sessions={sessions}
                windowState={windowState}
                inputState={inputState}
                elapsedSec={elapsedSec}
                awLive={awLive}
              />
            </div>
          </>
        )}

        {!loading && error && error !== 'not_found' && !daily && (
          <div className="px-4 py-6 font-mono text-xs text-amber-800">
            Could not load daily JSON ({error}). Is the proxy running on port 5899?
          </div>
        )}
      </main>
    </div>
  )
}
