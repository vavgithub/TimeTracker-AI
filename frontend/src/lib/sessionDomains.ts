/** Host labels for session URL chips (mock: zoom.us, clickup.com). */
const HOST_ALIASES: Record<string, string> = {
  'us06web.zoom.us': 'zoom.us',
  'us02web.zoom.us': 'zoom.us',
  'mail.google.com': 'gmail.com',
  'app.clickup.com': 'clickup.com',
  'developer.clickup.com': 'clickup.com',
  'docs.google.com': 'google docs',
  'drive.google.com': 'google drive',
  'console.cloud.google.com': 'google cloud',
}

function normalizeHost(host: string): string {
  const h = host.toLowerCase().replace(/^www\./, '')
  if (h.endsWith('.zoom.us') || h === 'zoom.us') return 'zoom.us'
  return HOST_ALIASES[h] ?? h
}

export function domainChipsFromUrls(urls: string[], max = 6): string[] {
  const seen = new Set<string>()
  for (const raw of urls || []) {
    const u = String(raw || '').trim()
    if (!u || u.startsWith('APP_CONTEXT:')) continue
    try {
      const href = u.includes('://') ? u : `https://${u}`
      const host = new URL(href).hostname
      if (!host || host === 'localhost' || host.startsWith('127.')) continue
      const label = normalizeHost(host)
      seen.add(label)
    } catch {
      /* ignore */
    }
    if (seen.size >= max) break
  }
  return [...seen]
}
