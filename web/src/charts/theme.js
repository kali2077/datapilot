// DataPilot ECharts 主题助手
// 运行时从 CSS 变量解析 --dp-* token:图表配色跟随浅/暗主题,
// 主题切换 via onThemeChange 重绘(echarts 配色在 setOption 时解析)。

const VAR_MAP = {
  accent: '--dp-accent',
  ok: '--dp-ok',
  warn: '--dp-warn',
  danger: '--dp-danger',
  idle: '--dp-idle',
  text2: '--dp-text-2',
  text3: '--dp-text-3',
  border: '--dp-border',
  surface: '--dp-surface',
}

export function dpColor(name = 'accent') {
  const v = getComputedStyle(document.documentElement).getPropertyValue(VAR_MAP[name]).trim()
  return v || '#2563eb'
}

function hexToRgba(hex, alpha) {
  let h = hex.replace('#', '')
  if (h.length === 3) h = h.split('').map((c) => c + c).join('')
  const n = parseInt(h, 16)
  if (Number.isNaN(n)) return hex
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`
}

export function dpAlpha(name, alpha) {
  return hexToRgba(dpColor(name), alpha)
}

// 多系列默认配色:单 accent 色透明度阶梯(dataviz 原则:hue 不编码数量,
// 透明度阶梯保持感知可排序且色盲安全;语义状态请显式用 ok/warn/danger)
export function dpSeriesColors(count) {
  const steps = [1, 0.62, 0.38, 0.22, 0.12]
  return Array.from({ length: count }, (_, i) => dpAlpha('accent', steps[Math.min(i, steps.length - 1)]))
}

export function dpAxisLabel(extra = {}) {
  return { color: dpColor('text3'), fontSize: 11, ...extra }
}

export function dpSplitLine() {
  return { lineStyle: { color: dpColor('border'), opacity: 0.6 } }
}

export function dpTooltip(extra = {}) {
  return {
    trigger: 'axis',
    backgroundColor: dpColor('surface'),
    borderColor: dpColor('border'),
    textStyle: { color: dpColor('text2'), fontSize: 12 },
    extraCssText: 'box-shadow: 0 4px 12px rgba(16,24,40,.12); border-radius: 8px;',
    ...extra,
  }
}

export function dpLegend(extra = {}) {
  return { textStyle: { color: dpColor('text3'), fontSize: 11 }, itemWidth: 12, itemHeight: 8, ...extra }
}

// 面积图渐变填充:线上 15% → 底部 0(dataviz 规范)
export function areaFade(color) {
  return {
    type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
    colorStops: [
      { offset: 0, color: dpAlpha(color, 0.16) },
      { offset: 1, color: dpAlpha(color, 0) },
    ],
  }
}

// 订阅主题切换(返回取消函数);cb 内用最新 token 重绘即可
export function onThemeChange(cb) {
  window.addEventListener('dp-theme-change', cb)
  return () => window.removeEventListener('dp-theme-change', cb)
}
