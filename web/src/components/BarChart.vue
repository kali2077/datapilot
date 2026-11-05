<template>
  <div ref="el" class="bar-chart" :style="{ height: height + 'px' }" />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  card: { type: Object, required: true },
  height: { type: Number, default: 280 },
})

const el = ref(null)
let chart = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const card = props.card
  const cols = card.columns || []
  const rows = card.rows || []
  const chartDef = card.chart || {}
  const xIdx = Math.max(0, cols.indexOf(chartDef.x))
  const yIdx = (chartDef.y || []).map((y) => cols.indexOf(y)).filter((i) => i >= 0)
  const xData = rows.map((r) => (r[xIdx] === null || r[xIdx] === undefined ? '' : String(r[xIdx])))
  const series = (chartDef.y || []).map((name, k) => ({
    name,
    type: 'bar',
    data: rows.map((r) => {
      const v = r[yIdx[k]]
      const n = Number(v)
      return Number.isNaN(n) ? 0 : n
    }),
    itemStyle: { borderRadius: [3, 3, 0, 0] },
    barMaxWidth: 36,
  }))
  chart.setOption({
    grid: { left: 48, right: 18, top: yIdx.length > 1 ? 36 : 24, bottom: 46 },
    tooltip: { trigger: 'axis' },
    legend: yIdx.length > 1 ? { top: 4 } : undefined,
    xAxis: {
      type: 'category',
      data: xData,
      axisLabel: {
        rotate: xData.length > 6 ? 28 : 0,
        interval: 0,
        width: 90,
        overflow: 'truncate',
      },
    },
    yAxis: { type: 'value' },
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
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
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
