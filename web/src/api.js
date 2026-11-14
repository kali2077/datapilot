import axios from 'axios'
import { ElMessage } from 'element-plus'

/** 统一错误信息提取：FastAPI detail 可能是字符串 / 数组 / 对象 */
export function errMsg(e) {
  const d = e?.response?.data?.detail ?? e?.response?.data?.message ?? e?.message
  if (Array.isArray(d)) {
    return d.map((x) => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`).join('；')
  }
  if (d && typeof d === 'object') return JSON.stringify(d)
  return d || '请求失败'
}

const http = axios.create({ baseURL: '', timeout: 120000 })

http.interceptors.request.use((cfg) => {
  const token = localStorage.getItem('dp_token')
  if (token) cfg.headers.Authorization = `Bearer ${token}`
  return cfg
})

let redirecting = false
http.interceptors.response.use(
  (res) => res.data,
  (e) => {
    if (e?.response?.status === 401) {
      localStorage.removeItem('dp_token')
      localStorage.removeItem('dp_user')
      if (!redirecting && !location.hash.startsWith('#/login')) {
        redirecting = true
        ElMessage.error('登录已过期，请重新登录')
        location.hash = '#/login'
        setTimeout(() => { redirecting = false }, 1200)
      }
    } else {
      ElMessage.error(errMsg(e))
    }
    return Promise.reject(e)
  }
)

export default http

/** 流式对话：fetch + SSE 解析，逐事件回调 onEvent(ev)，返回最终 done 事件。
 *  服务端事件：stage/delta/tool_begin/tool_end/done，error 事件抛异常。
 *  超时覆盖整个请求-流读取过程（Agent 多轮 + LLM 可能较慢，默认 5 分钟）。 */
export async function postSSE(url, body, onEvent, timeoutMs = 300000) {
  const token = localStorage.getItem('dp_token')
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  const finish = () => clearTimeout(timer)
  let res
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    })
  } catch {
    finish()
    throw new Error('请求超时或网络连接失败')
  }
  try {
    if (res.status === 401) {
      localStorage.removeItem('dp_token')
      localStorage.removeItem('dp_user')
      if (!location.hash.startsWith('#/login')) location.hash = '#/login'
      throw new Error('登录已过期，请重新登录')
    }
    if (!res.ok) {
      let detail = ''
      try {
        const j = await res.json()
        detail = j.detail ?? j.message ?? ''
      } catch { /* 非 JSON 错误体 */ }
      throw new Error(typeof detail === 'string' && detail ? detail : `请求失败（${res.status}）`)
    }
    const reader = res.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buf = ''
    let done = null
    for (;;) {
      const { value, done: finished } = await reader.read()
      if (finished) break
      buf += decoder.decode(value, { stream: true })
      const parts = buf.split('\n\n')
      buf = parts.pop()
      for (const part of parts) {
        const line = part.split('\n').find((l) => l.startsWith('data: '))
        if (!line) continue
        let ev
        try { ev = JSON.parse(line.slice(6)) } catch { continue }
        if (ev.type === 'error') throw new Error(ev.message || 'Agent 执行失败')
        if (ev.type === 'done') done = ev
        else onEvent?.(ev)
      }
    }
    if (!done) throw new Error('流式响应提前结束，未收到最终结果')
    return done
  } finally {
    finish()
  }
}

/** 与后端 DateTime 字符串一致的本地时间格式（YYYY-MM-DD HH:mm:ss）。 */
export function nowTs() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 时间/耗时格式化工具 */
export function fmtDuration(ms) {
  if (ms === null || ms === undefined || ms === '') return '-'
  const n = Number(ms)
  if (Number.isNaN(n)) return '-'
  if (n < 1000) return `${n} ms`
  const s = Math.round(n / 100) / 10
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  return `${m} min ${(Math.round(s - m * 60))} s`
}

export function timeDiff(start, end) {
  if (!start || !end) return '-'
  const t1 = new Date(String(start).replace(' ', 'T')).getTime()
  const t2 = new Date(String(end).replace(' ', 'T')).getTime()
  if (Number.isNaN(t1) || Number.isNaN(t2)) return '-'
  return fmtDuration(t2 - t1)
}
