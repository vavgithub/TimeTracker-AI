import type { TraceSession } from './trace'
import { APP_DOT } from './traceColors'

export type AppDotKind = keyof typeof APP_DOT

export interface TitleEntry {
  title: string
  subtitle?: string
  url?: string
  minutes: number
}

export interface AppBlock {
  app: string
  dotKind: AppDotKind
  totalMinutes: number
  entries: TitleEntry[]
}

const BROWSER_RE = /\s-\s*(Brave|Google Chrome|Chromium|Firefox|Chrome)\s*$/i
const CURSOR_RE = /\s-\s*Cursor\s*$/i
const VSCODE_RE = /\s-\s*(Code|Visual Studio Code|VS Code)\s*$/i

function normAppName(a: string): string {
  return a.trim()
}

function isBrowserApp(name: string): boolean {
  const n = name.toLowerCase()
  return ['brave', 'google-chrome', 'chrome', 'chromium', 'firefox'].some((x) => n.includes(x))
}

function isEditorApp(name: string): boolean {
  const n = name.toLowerCase()
  return (
    n.includes('cursor') ||
    n === 'code' ||
    n.includes('vscode') ||
    n.includes('pycharm') ||
    n.includes('webstorm')
  )
}

function dotKindForApp(app: string): AppDotKind {
  if (isBrowserApp(app) || app === 'Brave' || app === 'Google-chrome') return 'browser'
  if (isEditorApp(app)) return 'editor'
  return 'other'
}

/** Guess window title's app from title suffixes and tokens. */
export function classifyTitleToApp(title: string, sessionApps: string[]): string {
  const t = title.trim()
  const mBr = t.match(BROWSER_RE)
  if (mBr) return mBr[1].toLowerCase() === 'google chrome' ? 'Google-chrome' : mBr[1]
  if (CURSOR_RE.test(t)) return 'Cursor'
  if (VSCODE_RE.test(t)) return 'Code'
  const tl = t.toLowerCase()
  if (tl.includes('zoom')) return 'zoom'
  if (tl.includes('clickup')) return 'ClickUp'
  if (tl.includes('meet.google')) return 'Google Meet'
  if (tl.includes('teams.microsoft')) return 'Teams'

  const appsLower = new Set(sessionApps.map((a) => a.toLowerCase()))
  if (tl.includes('zoom') && appsLower.has('zoom')) return 'zoom'
  if (appsLower.has('brave') && (tl.includes('brave') || mBr)) return 'Brave'
  if (appsLower.has('google-chrome') && tl.includes('chrome')) return 'Google-chrome'

  const fallback =
    sessionApps.find((a) => isBrowserApp(a)) ||
    sessionApps.find((a) => isEditorApp(a)) ||
    sessionApps[0] ||
    'Other'
  return normAppName(fallback)
}

function parseCursorTitle(title: string): { title: string; subtitle?: string } {
  const t = title.replace(/\s-\s*Cursor\s*$/i, '').trim()
  const parts = t.split(/\s-\s*/)
  if (parts.length >= 2) {
    const filename = parts[0].trim()
    const project = parts.slice(1).join(' - ').trim()
    return { title: filename, subtitle: project }
  }
  return { title: t }
}

function stripBrowserSuffix(title: string): string {
  return title.replace(/\s-\s*(Brave|Google Chrome|Chromium|Firefox|Chrome)\s*$/i, '').trim()
}

function isAppContextUrl(u: string): boolean {
  return u.startsWith('APP_CONTEXT:')
}

/**
 * Pair titles[i] with urls[i] by index (pipeline convention).
 * Keeps rows so both title and URL stay visible, including tail indices with only one side set.
 */
function zipTitlesUrls(titles: string[], urls: string[]): { title: string; url?: string }[] {
  const n = Math.max(titles.length, urls.length)
  const out: { title: string; url?: string }[] = []
  for (let i = 0; i < n; i++) {
    const rawTitle = titles[i]
    const title = rawTitle != null ? String(rawTitle).trim() : ''
    let url: string | undefined = urls[i] != null ? String(urls[i]) : undefined
    if (url !== undefined && isAppContextUrl(url)) url = undefined
    if (!title && !url) continue
    out.push({ title: title || '(no title)', url })
  }
  return out
}

export function buildSessionAppBreakdown(session: TraceSession): AppBlock[] {
  const duration = Math.max(0, Number(session.duration_min) || 0)
  const pairs = zipTitlesUrls(session.titles || [], session.urls || [])
  if (pairs.length === 0) {
    const apps = session.apps || []
    if (apps.length === 0) return []
    return apps.map((app) => ({
      app: normAppName(app),
      dotKind: dotKindForApp(app),
      totalMinutes: duration,
      entries: [{ title: '(no window titles)', minutes: duration }],
    }))
  }

  const perTitleWeight = duration / pairs.length
  type Agg = { url?: string; count: number; sampleTitle: string }
  const byApp = new Map<string, Map<string, Agg>>()

  for (const { title, url } of pairs) {
    const app = classifyTitleToApp(title, session.apps || [])
    const key = title.trim()
    if (!byApp.has(app)) byApp.set(app, new Map())
    const m = byApp.get(app)!
    const cur = m.get(key)
    if (cur) {
      cur.count += 1
      if (!cur.url && url) cur.url = url
    } else {
      m.set(key, { url, count: 1, sampleTitle: title })
    }
  }

  const blocks: AppBlock[] = []
  for (const [app, titleMap] of byApp) {
    const entries: TitleEntry[] = []
    let total = 0
    for (const [, agg] of titleMap) {
      const mins = perTitleWeight * agg.count
      total += mins
      const raw = agg.sampleTitle
      let displayTitle = raw
      let subtitle: string | undefined
      let entryUrl = agg.url

      if (app === 'Cursor' || raw.toLowerCase().endsWith('cursor')) {
        const p = parseCursorTitle(raw)
        displayTitle = p.title
        subtitle = p.subtitle
      } else if (isBrowserApp(app) || BROWSER_RE.test(raw)) {
        displayTitle = stripBrowserSuffix(raw)
        if (entryUrl && !/^https?:\/\//i.test(entryUrl)) entryUrl = undefined
      }

      entries.push({
        title: displayTitle,
        subtitle,
        url: entryUrl,
        minutes: mins,
      })
    }
    entries.sort((a, b) => b.minutes - a.minutes)
    blocks.push({
      app: normAppName(app),
      dotKind: dotKindForApp(app),
      totalMinutes: total,
      entries,
    })
  }

  blocks.sort((a, b) => b.totalMinutes - a.totalMinutes)
  return blocks
}
