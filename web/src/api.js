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
