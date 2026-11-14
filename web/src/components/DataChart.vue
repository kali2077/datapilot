<template>
  <div ref="el" class="data-chart" :style="{ height: height + 'px' }" />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { dpAxisLabel, dpLegend, dpSeriesColors, dpSplitLine, dpTooltip, onThemeChange } from '../charts/theme'

// 问数结果图表：bar（横向条形，按数值降序）/ line（原始顺序，适合时序）/ pie（首指标占比，Top11+其他）
const props = defineProps({
  card: { type: Object, required: true },
  type: { type: String, default: 'bar' },
  height: { type: Number, default: 280 },
})

const el = ref(null)
let chart = null
let offTheme = null

function xy() {
  const card = props.card
  const cols = card.columns || []
  const rows = card.rows || []
  const chartDef = card.chart || {}
  const xIdx = Math.max(0, cols.indexOf(chartDef.x))
  const yNames = chartDef.y || []
  const yIdx = yNames.map((y) => cols.indexOf(y)).filter((i) => i >= 0)
  const xData = rows.map((r) => (r[xIdx] === null || r[xIdx] === undefined ? '' : String(r[xIdx])))
  const yData = (k) => rows.map((r) => {
    const n = Number(r[yIdx[k]])
    return Number.isNaN(n) ? 0 : n
  })
  return { xData, yNames, yData }
}

function renderBar() {
  const { xData, yNames, yData } = xy()
  // 分类对比 → 横向条形(dataviz:类目轴放 y,数值轴放 x,标签更可读)
  // 单系列时按数值降序排列,便于直接比较
  const rows = props.card.rows || []
  let order = rows.map((_, i) => i)
  if (yNames.length === 1) {
    const vals = yData(0)
    order = order.sort((a, b) => vals[b] - vals[a])
  }
  const colors = dpSeriesColors(yNames.length)
  const series = yNames.map((name, k) => ({
    name,
    type: 'bar',
    data: order.map((i) => yData(k)[i]),
    itemStyle: { borderRadius: [0, 3, 3, 0], color: colors[k] },
    barMaxWidth: 18,
  }))
  chart.setOption({
    grid: { left: 8, right: 28, top: yNames.length > 1 ? 30 : 12, bottom: 8, containLabel: true },
    tooltip: dpTooltip(),
    legend: yNames.length > 1 ? dpLegend({ top: 0, right: 0 }) : undefined,
    xAxis: { type: 'value', axisLabel: dpAxisLabel(), splitLine: dpSplitLine() },
    yAxis: {
      type: 'category',
      data: order.map((i) => xData[i]),
      inverse: true,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: dpAxisLabel({ width: 110, overflow: 'truncate' }),
    },
    series,
  }, true)
}

function renderLine() {
  // 折线：保留原始行序（时序语义），x 轴类目、y 轴数值
  const { xData, yNames, yData } = xy()
  const colors = dpSeriesColors(yNames.length)
  const series = yNames.map((name, k) => ({
    name,
    type: 'line',
    data: yData(k),
    smooth: true,
    showSymbol: xData.length <= 60,
    symbolSize: 5,
    lineStyle: { width: 2, color: colors[k] },
    itemStyle: { color: colors[k] },
  }))
  chart.setOption({
    grid: { left: 8, right: 16, top: yNames.length > 1 ? 30 : 16, bottom: 8, containLabel: true },
    tooltip: dpTooltip(),
    legend: yNames.length > 1 ? dpLegend({ top: 0, right: 0 }) : undefined,
    xAxis: {
      type: 'category',
      data: xData,
      axisTick: { show: false },
      axisLabel: dpAxisLabel({ width: 96, overflow: 'truncate', rotate: xData.length > 12 ? 30 : 0 }),
    },
    yAxis: { type: 'value', axisLabel: dpAxisLabel(), splitLine: dpSplitLine() },
    series,
  }, true)
}

function renderPie() {
  // 饼图：取第一个数值指标，按占比降序；类目过多时聚合 Top11 + 「其他」
  const { xData, yNames, yData } = xy()
  const k = 0
  const items = xData.map((name, i) => ({ name: name || `#${i + 1}`, value: yData(k)[i] }))
    .sort((a, b) => b.value - a.value)
  const top = items.slice(0, 11)
  if (items.length > 11) {
    top.push({ name: '其他', value: items.slice(11).reduce((s, x) => s + x.value, 0) })
  }
  chart.setOption({
    tooltip: dpTooltip({ trigger: 'item' }),
    legend: dpLegend({ type: 'scroll', right: 0, top: 0 }),
    series: [{
      type: 'pie',
      radius: ['36%', '66%'],
      center: ['50%', '58%'],
      data: top,
      itemStyle: { borderRadius: 4 },
      label: { color: dpAxisLabel().color, fontSize: 11, formatter: '{b} {d}%' },
      emphasis: { label: { formatter: '{b}\n{c} ({d}%)' } },
    }],
  }, true)
}

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  if (props.type === 'pie') renderPie()
  else if (props.type === 'line') renderLine()
  else renderBar()
  chart.resize()
}

function getDataURL() {
  // 导出 PNG：底色取容器实际背景（暗色主题下不透明）
  if (!chart) return null
  let bg = '#ffffff'
  try {
    bg = getComputedStyle(el.value).backgroundColor || '#ffffff'
  } catch { /* keep default */ }
  return chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: bg })
}

function onResize() {
  chart && chart.resize()
}

onMounted(() => {
  render()
  window.addEventListener('resize', onResize)
  offTheme = onThemeChange(render)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (offTheme) offTheme()
  if (chart) {
    chart.dispose()
    chart = null
  }
})

watch(() => [props.card, props.type], render, { deep: true })

defineExpose({ getDataURL })
</script>

<style scoped>
.data-chart {
  width: 100%;
}
</style>
