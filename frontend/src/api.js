const TOKEN_KEY = 'redis_data_downloader_token'
const USER_KEY = 'redis_data_downloader_username'

export function getToken() {
  return sessionStorage.getItem(TOKEN_KEY)
}

export function getUsername() {
  return sessionStorage.getItem(USER_KEY)
}

export function setSession(token, username) {
  sessionStorage.setItem(TOKEN_KEY, token)
  sessionStorage.setItem(USER_KEY, username)
}

export function clearSession() {
  sessionStorage.removeItem(TOKEN_KEY)
  sessionStorage.removeItem(USER_KEY)
}

async function parseError(res) {
  try {
    const data = await res.json()
    if (typeof data?.detail === 'string') return data.detail
    if (Array.isArray(data?.detail)) {
      return data.detail.map((d) => d.msg || JSON.stringify(d)).join(', ')
    }
    return JSON.stringify(data)
  } catch {
    return res.statusText || 'Request failed'
  }
}

export async function login(username, password) {
  const res = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function fetchFields() {
  const res = await fetch('/api/fields', {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function fetchHistory() {
  const res = await fetch('/api/history', {
    headers: { Authorization: `Bearer ${getToken()}` },
  })
  if (!res.ok) throw new Error(await parseError(res))
  return res.json()
}

export async function exportData({ fields, imeis }) {
  const res = await fetch('/api/export', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
    },
    body: JSON.stringify({ fields, imeis }),
  })
  if (!res.ok) throw new Error(await parseError(res))

  const blob = await res.blob()
  const disposition = res.headers.get('Content-Disposition') || ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const filename = match?.[1] || 'export.xlsx'
  return { blob, filename }
}
