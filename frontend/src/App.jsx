import { useCallback, useEffect, useMemo, useState } from 'react'
import Clarity from '@microsoft/clarity'
import {
  clearSession,
  exportData,
  fetchFields,
  fetchHistory,
  getToken,
  getUsername,
  login,
  setSession,
} from './api'
import './App.css'

function groupFields(fields) {
  const groups = new Map()
  for (const f of fields) {
    if (f.always) continue
    if (!groups.has(f.group)) groups.set(f.group, [])
    groups.get(f.group).push(f)
  }
  return groups
}

function parseImeis(text) {
  return text
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean)
}

function LoginView({ onSuccess }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      const data = await login(username.trim(), password)
      setSession(data.access_token, data.username)
      Clarity.identify(data.username, undefined, undefined, data.username)
      onSuccess(data.username)
    } catch (err) {
      setError(err.message || 'Login failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="shell login-shell">
      <header className="brand">
        <h1>redis-data-downloader</h1>
        <p>Export Redis IMEI telemetry to Excel</p>
      </header>
      <form className="login-form" onSubmit={handleSubmit}>
        <label>
          Username
          <input
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label>
          Password
          <input
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error ? <p className="error">{error}</p> : null}
        <button type="submit" disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </div>
  )
}

function ExportView({ username, onLogout }) {
  const [fields, setFields] = useState([])
  const [selected, setSelected] = useState(() => new Set())
  const [scope, setScope] = useState('all')
  const [imeiText, setImeiText] = useState('')
  const [runs, setRuns] = useState([])
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [fieldRes, histRes] = await Promise.all([fetchFields(), fetchHistory()])
      setFields(fieldRes.fields || [])
      setSelected(new Set(fieldRes.defaults || []))
      setRuns(histRes.runs || [])
    } catch (err) {
      setError(err.message || 'Failed to load')
      if (String(err.message).toLowerCase().includes('token') || String(err.message).includes('401')) {
        onLogout()
      }
    } finally {
      setLoading(false)
    }
  }, [onLogout])

  useEffect(() => {
    load()
  }, [load])

  const groups = useMemo(() => groupFields(fields), [fields])

  function toggleField(id) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  function selectGroup(groupFieldsList, on) {
    setSelected((prev) => {
      const next = new Set(prev)
      for (const f of groupFieldsList) {
        if (on) next.add(f.id)
        else next.delete(f.id)
      }
      return next
    })
  }

  async function handleDownload() {
    setError('')
    setStatus('')
    setBusy(true)
    try {
      const imeis = scope === 'specific' ? parseImeis(imeiText) : null
      if (scope === 'specific' && (!imeis || imeis.length === 0)) {
        throw new Error('Enter at least one IMEI')
      }
      const { blob, filename } = await exportData({
        fields: Array.from(selected),
        imeis,
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      a.click()
      URL.revokeObjectURL(url)
      setStatus(`Downloaded ${filename}`)
      const histRes = await fetchHistory()
      setRuns(histRes.runs || [])
    } catch (err) {
      setError(err.message || 'Export failed')
    } finally {
      setBusy(false)
    }
  }

  if (loading) {
    return (
      <div className="shell">
        <p className="muted">Loading…</p>
      </div>
    )
  }

  return (
    <div className="shell export-shell">
      <header className="topbar">
        <div>
          <h1>redis-data-downloader</h1>
          <p className="muted">Signed in as {username}</p>
        </div>
        <button type="button" className="ghost" onClick={onLogout}>
          Log out
        </button>
      </header>

      <div className="layout">
        <main className="main">
          <section>
            <h2>Download Redis data</h2>
            <p className="muted">Choose fields, then export all batteries or specific IMEIs.</p>
          </section>

          <section className="scope">
            <h3>IMEI scope</h3>
            <label className="radio">
              <input
                type="radio"
                name="scope"
                checked={scope === 'all'}
                onChange={() => setScope('all')}
              />
              All IMEIs
            </label>
            <label className="radio">
              <input
                type="radio"
                name="scope"
                checked={scope === 'specific'}
                onChange={() => setScope('specific')}
              />
              Specific IMEIs
            </label>
            {scope === 'specific' ? (
              <textarea
                placeholder="One IMEI per line, or comma-separated"
                value={imeiText}
                onChange={(e) => setImeiText(e.target.value)}
                rows={6}
              />
            ) : null}
          </section>

          <section className="fields">
            <h3>Fields</h3>
            {[...groups.entries()].map(([group, list]) => {
              const allOn = list.every((f) => selected.has(f.id))
              return (
                <details key={group} open={group.startsWith('Redis — top') || group.includes('Center') || group === 'Identity'}>
                  <summary>
                    <span>{group}</span>
                    <button
                      type="button"
                      className="linkish"
                      onClick={(e) => {
                        e.preventDefault()
                        selectGroup(list, !allOn)
                      }}
                    >
                      {allOn ? 'Clear' : 'Select all'}
                    </button>
                  </summary>
                  <div className="checkbox-grid">
                    {list.map((f) => (
                      <label key={f.id} className="check">
                        <input
                          type="checkbox"
                          checked={selected.has(f.id)}
                          onChange={() => toggleField(f.id)}
                        />
                        <span>{f.label}</span>
                      </label>
                    ))}
                  </div>
                </details>
              )
            })}
          </section>

          {error ? <p className="error">{error}</p> : null}
          {status ? <p className="ok">{status}</p> : null}

          <button type="button" className="primary" disabled={busy} onClick={handleDownload}>
            {busy ? 'Exporting…' : 'Download XLSX'}
          </button>
        </main>

        <aside className="history">
          <h2>Run history</h2>
          {runs.length === 0 ? (
            <p className="muted">No runs yet.</p>
          ) : (
            <ul>
              {runs.map((run, i) => (
                <li key={`${run.started_at}-${i}`}>
                  <strong>{run.username}</strong>
                  <span>{run.started_at}</span>
                  <span className="muted">
                    {run.scope} · {run.row_count} rows
                  </span>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  )
}

export default function App() {
  const [username, setUsername] = useState(() => (getToken() ? getUsername() : null))

  useEffect(() => {
    if (username) {
      Clarity.identify(username, undefined, undefined, username)
    }
  }, [username])

  function handleLogout() {
    clearSession()
    setUsername(null)
  }

  if (!username) {
    return <LoginView onSuccess={setUsername} />
  }

  return <ExportView username={username} onLogout={handleLogout} />
}
