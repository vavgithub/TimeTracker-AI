const TZ = 'Asia/Kolkata'

export function parseIsoToDate(iso: string): Date {
  const s = iso.replace(/(\.\d{3})\d+/, '$1')
  const d = new Date(s)
  return Number.isNaN(d.getTime()) ? new Date(0) : d
}

export function formatDateHeaderIST(isoDate: string): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  if (!y || !m || !d) return isoDate
  const utc = Date.UTC(y, m - 1, d, 12, 0, 0)
  return new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: TZ,
  }).format(new Date(utc))
}

export function formatTimeRangeIST(startIso: string, endIso: string): string {
  const fmt = new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: TZ,
  })
  return `${fmt.format(parseIsoToDate(startIso))}→${fmt.format(parseIsoToDate(endIso))}`
}

export function formatTimeHM_IST(iso: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: TZ,
  }).format(parseIsoToDate(iso))
}

export function formatClockIST(d: Date): string {
  return new Intl.DateTimeFormat('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: TZ,
  }).format(d)
}

/** e.g. 12:13pm (mock scheduler) */
export function formatClock12AmpmIST(d: Date): string {
  return new Intl.DateTimeFormat('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
    timeZone: TZ,
  })
    .format(d)
    .replace(/\s+/g, '')
    .toLowerCase()
}

export function todayISOInIST(): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const y = parts.find((p) => p.type === 'year')?.value
  const m = parts.find((p) => p.type === 'month')?.value
  const d = parts.find((p) => p.type === 'day')?.value
  return `${y}-${m}-${d}`
}

export function addDaysISO(isoDate: string, delta: number): string {
  const [y, m, d] = isoDate.split('-').map(Number)
  const base = new Date(Date.UTC(y, m - 1, d))
  base.setUTCDate(base.getUTCDate() + delta)
  const yy = base.getUTCFullYear()
  const mm = String(base.getUTCMonth() + 1).padStart(2, '0')
  const dd = String(base.getUTCDate()).padStart(2, '0')
  return `${yy}-${mm}-${dd}`
}

export function istDayStartISO(isoDate: string): string {
  return `${isoDate}T00:00:00+05:30`
}
