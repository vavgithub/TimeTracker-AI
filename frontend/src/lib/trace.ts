/** Normalized session for Trace UI (merged from writer `sessions_*.json`). */
export interface TraceSessionInput {
  keystrokes: number
  mouse_clicks: number
  activity_rate: number
  scroll_units?: number
}

export interface AppBreakdownTab {
  title: string
  project?: string
  url?: string | null
  minutes: number
}

export interface AppBreakdownBlock {
  app: string
  total_minutes: number
  is_browser: boolean
  is_editor: boolean
  tabs: AppBreakdownTab[]
}

export type TraceZone = 'task_linked' | 'meeting' | 'untracked_work' | 'untracked' | 'unknown' | 'unclear' | string

export interface TraceSession {
  session_id?: string
  start: string
  end: string
  duration_min: number
  zone: TraceZone
  clickup_task_id: string | null
  clickup_task_name: string | null
  map_confidence: number
  map_method: string
  map_notes?: string
  input: TraceSessionInput
  apps: string[]
  titles: string[]
  urls: string[]
  /** From pipeline `build_app_breakdown`; preferred for expanded view */
  app_breakdown?: AppBreakdownBlock[]
}

export interface DailyTotalsTrace {
  active_minutes: number
  idle_minutes: number
  productivity_pct?: number
  activity?: number
  session_count?: number
  task_linked_minutes?: number
  meeting_minutes?: number
  untracked_minutes?: number
  unclear_minutes?: number
  unknown_minutes?: number
  keystrokes?: number
  mouse_clicks?: number
}

export interface DomainTopRow {
  domain: string
  seconds?: number
  minutes: number
}

export interface DailyJsonTrace {
  date: string
  user: string
  totals: DailyTotalsTrace
  breakdown?: Record<string, number>
  web_domain_summary?: {
    domains_top?: DomainTopRow[]
  }
  generated_at?: string
}

export interface WriterSessionRaw {
  session_id?: string
  start: string
  end: string
  duration_min: number
  apps?: string[]
  titles?: string[]
  urls?: string[]
  input?: {
    keystrokes?: number
    mouse_clicks?: number
    activity_rate?: number
    scroll_units?: number
    active_minutes?: number
    idle_minutes?: number
  }
  app_breakdown?: AppBreakdownBlock[]
  ai_enrichment?: {
    zone?: string
    clickup_task_id?: string | null
    clickup_task_name?: string | null
    map_confidence?: number
    map_method?: string
    map_notes?: string
  }
}
