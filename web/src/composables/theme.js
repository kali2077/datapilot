import { ref, watchEffect } from 'vue'

// 全局主题状态(模块级单例):isDark + toggle,持久化到 localStorage
const STORAGE_KEY = 'dp_theme'

function resolveInitial() {
  const saved = localStorage.getItem(STORAGE_KEY)
  if (saved === 'dark') return true
  if (saved === 'light') return false
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches || false
}

const isDark = ref(resolveInitial())

function apply(dark) {
  document.documentElement.classList.toggle('dark', dark)
}

watchEffect(() => apply(isDark.value))

export function useTheme() {
  function toggle() {
    isDark.value = !isDark.value
    localStorage.setItem(STORAGE_KEY, isDark.value ? 'dark' : 'light')
    // 广播给图表等需要重绘的消费者(见 charts/theme.js)
    window.dispatchEvent(new CustomEvent('dp-theme-change', { detail: { dark: isDark.value } }))
  }
  return { isDark, toggle }
}
