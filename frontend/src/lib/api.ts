const apiBase = ((import.meta.env.VITE_API_BASE as string | undefined) ?? '').trim().replace(/\/+$/, '')

export function apiUrl(path: string): string {
  return apiBase + path
}

export function voiceSocketUrl(): string {
  const url = new URL(apiUrl('/ws/session'), window.location.origin)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.href
}
