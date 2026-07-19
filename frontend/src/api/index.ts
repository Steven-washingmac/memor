const BASE = ''

async function get(path: string) {
  const resp = await fetch(`${BASE}${path}`)
  return resp.json()
}

async function post(path: string, body?: any) {
  const resp = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  return resp.json()
}

async function del(path: string) {
  const resp = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  return resp.json()
}

// Auth
export const getQrCode = () => get('/api/auth/qr')
export const checkScan = (uuid: string) => get(`/api/auth/scan/${uuid}`)
export const login = (code: string) => post('/api/auth/login', { code })
export const sseUrl = (uuid: string) => `${BASE}/api/auth/scan/${uuid}/sse`

// Session
export const checkSession = () => get('/api/session/check')
export const logout = () => del('/api/session')

// Run
export const getRunPaper = () => get('/api/run/paper')
export const generateRoute = (data: any) => post('/api/run/generate', data)
export const submitRun = (data: any) => post('/api/run/submit', data)

// History
export const getCalendar = () => get('/api/history/calendar')
export const getTasks = () => get('/api/history/tasks')
