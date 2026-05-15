// OWNS: Phoenix WebSocket session lifecycle for the realtime job-event feed.
// NOT OWNS: HTTP fetching (api.js), application state (MarketContext).
//
// INVARIANTS:
// - The constructor never throws. The Socket is created lazily and any
//   failure (no token, Elixir down, network blocked) is a silent no-op so
//   the existing SSE + 5 s polling stay as the safety net.
// - `start()` is idempotent. Calling it twice is harmless.
// - When connected, the `connected` flag exposed via `isConnected()` flips
//   to true; callers use it to slow their reconciliation poll from 5 s to 60 s.
//
// DECISIONS:
// - Token TTL is 5 minutes server-side. We refresh ~60 s before expiry to
//   ride out clock skew without a flicker.
// - We never block the React render path: every async call is fire-and-forget.

import { Socket } from 'phoenix'
import { fetchSocketToken } from '../api'

// Path is reverse-proxied by Caddy to the Elixir Endpoint on port 4000.
// Mirror Caddy's directive: /elixir/socket → 127.0.0.1:4000/socket
const SOCKET_PATH = '/elixir/socket'
const RECONNECT_BACKOFF_MS = [1_000, 2_000, 5_000, 10_000, 30_000]
const HEARTBEAT_INTERVAL_MS = 30_000
const REFRESH_BEFORE_EXPIRY_MS = 60_000
const CONNECT_TIMEOUT_MS = 5_000

export class RealtimeSession {
  constructor({ apiKey, onJobEvent, onStateChange } = {}) {
    this._apiKey = apiKey
    this._onJobEvent = typeof onJobEvent === 'function' ? onJobEvent : () => {}
    this._onStateChange = typeof onStateChange === 'function' ? onStateChange : () => {}
    this._socket = null
    this._channel = null
    this._refreshTimer = null
    this._closed = false
    this._connected = false
  }

  isConnected() { return this._connected }

  async start() {
    if (this._closed || this._socket) return
    let creds
    try {
      creds = await fetchSocketToken(this._apiKey)
    } catch {
      // Network error during token fetch — give up; fallback paths handle it.
      return
    }
    if (!creds || !creds.token || this._closed) return
    this._openSocket(creds)
  }

  close() {
    this._closed = true
    this._setConnected(false)
    clearTimeout(this._refreshTimer)
    this._refreshTimer = null
    try { this._channel?.leave() } catch { /* swallow — close is best-effort */ }
    this._channel = null
    try { this._socket?.disconnect() } catch { /* swallow — close is best-effort */ }
    this._socket = null
  }

  _openSocket(creds) {
    const proto = typeof window !== 'undefined' && window.location?.protocol === 'https:' ? 'wss' : 'ws'
    const host = typeof window !== 'undefined' ? window.location.host : 'localhost'
    const endpoint = `${proto}://${host}${SOCKET_PATH}`

    const socket = new Socket(endpoint, {
      params: { token: creds.token },
      heartbeatIntervalMs: HEARTBEAT_INTERVAL_MS,
      reconnectAfterMs: (tries) =>
        RECONNECT_BACKOFF_MS[Math.min(tries - 1, RECONNECT_BACKOFF_MS.length - 1)],
      timeout: CONNECT_TIMEOUT_MS,
    })

    socket.onOpen(() => this._joinUserChannel(socket, creds))
    socket.onClose(() => this._setConnected(false))
    socket.onError(() => this._setConnected(false))

    this._socket = socket
    socket.connect()
    this._scheduleRefresh(creds)
  }

  _joinUserChannel(socket, creds) {
    if (this._closed) return
    // user_id is the SECOND segment of the token (v1.<user>.<exp>.<sig>).
    const tokenParts = creds.token.split('.')
    if (tokenParts.length !== 4) return
    const userId = tokenParts[1]
    if (!userId) return

    const channel = socket.channel(`user:${userId}`, {})
    channel.on('job_event', (payload) => {
      try { this._onJobEvent(payload) } catch { /* never crash the socket loop */ }
    })
    channel.join()
      .receive('ok', () => this._setConnected(true))
      .receive('error', () => this._setConnected(false))
      .receive('timeout', () => this._setConnected(false))

    this._channel = channel
  }

  _scheduleRefresh(creds) {
    clearTimeout(this._refreshTimer)
    const expiresMs = (creds.expires_at ?? 0) * 1_000
    const delay = expiresMs - Date.now() - REFRESH_BEFORE_EXPIRY_MS
    if (!Number.isFinite(delay) || delay <= 0) return
    this._refreshTimer = setTimeout(() => this._rotate(), delay)
  }

  async _rotate() {
    if (this._closed) return
    let next
    try { next = await fetchSocketToken(this._apiKey) }
    catch { return /* fallback paths cover the gap */ }
    if (!next || !next.token || this._closed) return
    try { this._socket?.disconnect() } catch { /* swallow */ }
    this._setConnected(false)
    this._socket = null
    this._channel = null
    this._openSocket(next)
  }

  _setConnected(value) {
    if (this._connected === value) return
    this._connected = value
    try { this._onStateChange(value) } catch { /* swallow */ }
  }
}
