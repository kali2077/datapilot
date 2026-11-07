<template>
  <div ref="el" class="bar-chart" :style="{ height: height + 'px' }" />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { dpAxisLabel, dpColor, dpLegend, dpSeriesColors, dpSplitLine, dpTooltip, onThemeChange } from '../charts/theme'

const props = defineProps({
  card: { type: Object, required: true },
  height: { type: Number, default: 280 },
})

const el = ref(null)
let chart = null
let offTheme = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const card = props.card
  const cols = card.columns || []
  const rows = card.rows || []
  const chartDef = card.chart || {}
  const xIdx = Math.max(0, cols.indexOf(chartDef.x))
  const yNames = chartDef.y || []
  const yIdx = yNames.map((y) => cols.indexOf(y)).filter((i) => i >= 0)
  const xData = rows.map((r) => (r[xIdx] === null || r[xIdx] === undefined ? '' : String(r[xIdx])))

  // 分类对比 → 横向条形(dataviz:类目轴放 y,数值轴放 x,标签更可读)
  // 单系列时按数值降序排列,便于直接比较
  let order = rows.map((_, i) => i)
  if (yIdx.length === 1) {
    const val = (i) => {
      const n = Number(rows[i][yIdx[0]])
      return Number.isNaN(n) ? 0 : n
    }
    order = order.sort((a, b) => val(b) - val(a))
  }
  const catData = order.map((i) => xData[i])

  const colors = dpSeriesColors(yNames.length)
  const series = yNames.map((name, k) => ({
    name,
    type: 'bar',
    data: order.map((i) => {
      const n = Number(rows[i][yIdx[k]])
      return Number.isNaN(n) ? 0 : n
    }),
    itemStyle: { borderRadius: [0, 3, 3, 0], color: colors[k] },
    barMaxWidth: 18,
  }))

  chart.setOption({
    grid: { left: 8, right: 28, top: yIdx.length > 1 ? 30 : 12, bottom: 8, containLabel: true },
    tooltip: dpTooltip(),
    legend: yIdx.length > 1 ? dpLegend({ top: 0, right: 0 }) : undefined,
    xAxis: {
      type: 'value',
      axisLabel: dpAxisLabel(),
      splitLine: dpSplitLine(),
    },
    yAxis: {
      type: 'category',
      data: catData,
      inverse: true,
      axisTick: { show: false },
      axisLine: { show: false },
      axisLabel: dpAxisLabel({ width: 110, overflow: 'truncate' }),
    },
    series,
  }, true)
  chart.resize()
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

watch(() => props.card, render, { deep: true })
</script>

<style scoped>
.bar-chart {
  width: 100%;
}
</style>
