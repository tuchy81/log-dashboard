export interface Resp<T = any> { data: T; meta: Record<string, any> }

export function qs(params: Record<string, any> = {}) {
  const u = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') u.set(k, String(v))
  const s = u.toString()
  return s ? '?' + s : ''
}

export async function get<T = any>(path: string, params: Record<string, any> = {}): Promise<Resp<T>> {
  const r = await fetch('/api/intel/v1' + path + qs(params))
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export async function patch<T = any>(path: string, body: any): Promise<Resp<T>> {
  const r = await fetch('/api/intel/v1' + path, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  if (!r.ok) throw new Error(`${r.status} ${path}`)
  return r.json()
}

export const csvUrl = (path: string, params: Record<string, any> = {}) => '/api/intel/v1' + path + qs({ ...params, export: 'csv' })
export const n = (v: any) => (v == null ? '-' : Number(v).toLocaleString('ko-KR'))
export const pct = (v: number) => (v * 100).toFixed(1) + '%'
export const COLOR = { ok: '#2a9d6f', warn: '#e0a100', bad: '#d64545', neutral: '#5b7a99', grid: '#e3e8ee' }
