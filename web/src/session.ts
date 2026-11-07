import { reactive } from 'vue'
import { API_URL } from './config'
import { jsonField } from './http'

interface Credentials {
  clientId: string
  clientSecret: string
}

export const EXPIRED = 'Your session has expired. Please log in again.'

// Memory only: a page reload logs the user out.
export const session = reactive({ token: '', clientId: '', notice: '' })
let credentials: Credentials | null = null
// One renewal shared by all requests that got a 401 at the same time.
let renewal: Promise<string> | null = null

class WrongCredentials extends Error {}

async function requestToken(c: Credentials): Promise<string> {
  const body = new URLSearchParams({ client_id: c.clientId, client_secret: c.clientSecret })
  const res = await fetch(`${API_URL}/oauth/token`, { method: 'POST', body })
  if (res.status === 401) throw new WrongCredentials('Wrong client id or secret')
  if (!res.ok) throw new Error(`Login failed (HTTP ${res.status})`)
  const token = await jsonField(res, 'access_token')
  if (typeof token !== 'string' || !token) throw new Error('Login failed (bad token response)')
  return token
}

export async function login(clientId: string, clientSecret: string): Promise<void> {
  const c = { clientId, clientSecret }
  session.token = await requestToken(c)
  credentials = c
  session.clientId = clientId
  session.notice = ''
}

export function logout(notice = ''): void {
  session.token = ''
  session.clientId = ''
  session.notice = notice
  credentials = null
  renewal = null
}

async function renewToken(): Promise<boolean> {
  const c = credentials
  if (c === null) return false
  renewal ??= requestToken(c)
  const current = renewal
  try {
    const token = await current
    if (credentials !== c) return false // logged out meanwhile
    session.token = token
    return true
  } catch (e) {
    // Only rejected credentials end the session; outages are retried later.
    if (e instanceof WrongCredentials && credentials === c) logout(EXPIRED)
    return false
  } finally {
    if (renewal === current) renewal = null
  }
}

/** Calls the API with the token; on a 401 renews the token once and retries.
 * A 401 returned while `session.token` is still set is temporary: do not log out on it. */
export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const send = (token: string) => {
    const headers = new Headers(init.headers)
    headers.set('Authorization', `Bearer ${token}`)
    return fetch(`${API_URL}${path}`, { ...init, headers })
  }
  const owner = credentials
  const sent = session.token
  const first = await send(sent)
  // A 401 from a previous session must not touch a newer one.
  if (first.status !== 401 || credentials !== owner) return first
  // Another request may already have renewed the token.
  if (session.token === sent && !(await renewToken())) return first
  if (!session.token || credentials !== owner) return first
  const retried = session.token
  const second = await send(retried)
  if (second.status === 401 && session.token === retried) logout(EXPIRED)
  return second
}
