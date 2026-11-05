<template>
  <div class="lineage-wrap">
    <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px">
      <span style="font-weight: 600">血缘关系图</span>
      <el-radio-group v-model="depth" size="small" @change="load">
        <el-radio-button :value="1">1 跳</el-radio-button>
        <el-radio-button :value="2">2 跳</el-radio-button>
        <el-radio-button :value="3">3 跳</el-radio-button>
      </el-radio-group>
      <el-button size="small" icon="Refresh" circle @click="load" />
      <div class="spacer" style="flex: 1" />
      <span v-if="graph" class="muted">{{ graph.nodes.length }} 节点 / {{ graph.edges.length }} 条边</span>
    </div>

    <div v-if="loading" v-loading="true" style="height: 260px" />
    <el-empty v-else-if="!graph || graph.nodes.length === 0" description="暂无血缘数据" :image-size="80" />
    <div v-else style="overflow: auto">
      <svg :width="layout.width" :height="layout.height" style="display: block; min-width: 100%">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8" />
          </marker>
        </defs>

        <!-- 连线 -->
        <g v-for="(e, i) in layout.edges" :key="'e' + i" style="cursor: pointer">
          <path :d="e.d" fill="none" stroke="transparent" stroke-width="12" />
          <path :d="e.d" fill="none" stroke="#94a3b8" stroke-width="1.5" marker-end="url(#arrow)" />
          <title>{{ e.tip }}</title>
        </g>

        <!-- 节点 -->
        <g v-for="(n, i) in layout.nodes" :key="'n' + i" style="cursor: default">
          <rect
            :x="n.x" :y="n.y" :width="NW" :height="NH" rx="9"
            :fill="n.fill" :stroke="n.center ? '#1e40af' : 'none'" stroke-width="2"
            style="filter: drop-shadow(0 1px 2px rgba(16,24,40,0.18))"
          />
          <text :x="n.x + NW / 2" :y="n.y + 23" text-anchor="middle" fill="#fff" font-size="13" font-weight="600">
            {{ n.shortName }}
          </text>
          <text :x="n.x + NW / 2" :y="n.y + 40" text-anchor="middle" fill="rgba(255,255,255,0.78)" font-size="11">
            {{ n.shortDs }}
          </text>
          <title>{{ n.tip }}</title>
        </g>
      </svg>
    </div>

    <div class="lineage-legend">
      <span><span class="dot" style="background: #2563eb" />中心表</span>
      <span><span class="dot" style="background: #16a34a" />上游（左侧）</span>
      <span><span class="dot" style="background: #ea580c" />下游（右侧）</span>
      <span class="muted">悬停连线可查看作业类型</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import http from '../api'

const props = defineProps({
  datasourceId: { type: Number, required: true },
  table: { type: String, required: true },
})

const NW = 168
const NH = 52
const GX = 84
const GY = 30

const depth = ref(2)
const loading = ref(false)
const graph = ref(null)

async function load() {
  loading.value = true
  try {
    graph.value = await http.get('/api/asset/map/graph', {
      params: { datasource_id: props.datasourceId, table: props.table, depth: depth.value },
    })
  } catch {
    graph.value = { nodes: [], edges: [], center: '' }
  } finally {
    loading.value = false
  }
}

const layout = computed(() => {
  const g = graph.value || { nodes: [], edges: [], center: '' }
  const upDepth = new Map()
  const downDepth = new Map()
  upDepth.set(g.center, 0)
  downDepth.set(g.center, 0)

  let frontier = [g.center]
  let d = 0
  while (frontier.length && d < depth.value) {
    const nxt = []
    for (const id of frontier) {
      for (const e of g.edges) {
        if (e.target === id && !upDepth.has(e.source)) {
          upDepth.set(e.source, d + 1)
          nxt.push(e.source)
        }
      }
    }
    frontier = nxt
    d += 1
  }
  frontier = [g.center]
  d = 0
  while (frontier.length && d < depth.value) {
    const nxt = []
    for (const id of frontier) {
      for (const e of g.edges) {
        if (e.source === id && !downDepth.has(e.target)) {
          downDepth.set(e.target, d + 1)
          nxt.push(e.target)
        }
      }
    }
    frontier = nxt
    d += 1
  }

  const colOf = new Map()
  for (const n of g.nodes) {
    const u = upDepth.get(n.id)
    const w = downDepth.get(n.id)
    if (u === undefined && w === undefined) continue
    colOf.set(n.id, (w ?? 0) - (u ?? 0))
  }

  const cols = new Map()
  for (const [id, c] of colOf) {
    if (!cols.has(c)) cols.set(c, [])
    cols.get(c).push(id)
  }

  const maxRows = Math.max(1, ...Array.from(cols.values()).map((v) => v.length))
  const midY = (maxRows * (NH + GY) - GY) / 2

  const pos = new Map()
  const colKeys = Array.from(cols.keys()).sort((a, b) => a - b)
  for (const c of colKeys) {
    const ids = cols.get(c)
    const h = ids.length * (NH + GY) - GY
    ids.forEach((id, i) => pos.set(id, { x: c * (NW + GX), y: midY - h / 2 + i * (NH + GY) }))
  }

  const nodeInfo = new Map()
  for (const n of g.nodes) {
    const p = pos.get(n.id)
    if (!p) continue
    const col = colOf.get(n.id) ?? 0
    const isCenter = n.id === g.center
    const fill = isCenter ? '#2563eb' : col < 0 ? '#16a34a' : col > 0 ? '#ea580c' : '#475569'
    nodeInfo.set(n.id, {
      ...p,
      fill,
      center: isCenter,
      shortName: n.table.length > 20 ? `${n.table.slice(0, 19)}…` : n.table,
      shortDs: (n.datasource || '').length > 18 ? `${n.datasource.slice(0, 17)}…` : n.datasource || '',
      tip: `${n.datasource}.${n.table}${isCenter ? '（中心表）' : ''}`,
    })
  }

  const byId = new Map(g.nodes.map((n) => [n.id, n]))
  const edges = []
  for (const e of g.edges) {
    const s = nodeInfo.get(e.source)
    const t = nodeInfo.get(e.target)
    if (!s || !t) continue
    const sx = s.x + NW
    const sy = s.y + NH / 2
    const tx = t.x
    const ty = t.y + NH / 2
    const mx = Math.max(30, Math.abs(tx - sx) / 2)
    const dd = `M ${sx} ${sy} C ${sx + mx} ${sy}, ${tx - mx} ${ty}, ${tx} ${ty}`
    const src = byId.get(e.source)
    const tgt = byId.get(e.target)
    const jt = e.job_type || 'sync'
    edges.push({
      d: dd,
      tip: `${src?.datasource || ''}.${src?.table || ''}  →  ${tgt?.datasource || ''}.${tgt?.table || ''}\n作业类型: ${jt}\n工作流: #${e.workflow_id ?? '-'}`,
    })
  }

  const minCol = colKeys.length ? colKeys[0] : 0
  const maxCol = colKeys.length ? colKeys[colKeys.length - 1] : 0
  const width = (maxCol - minCol) * (NW + GX) + NW + 40
  const height = Math.max(maxRows * (NH + GY) - GY, NH) + 20

  return { nodes: Array.from(nodeInfo.values()), edges, width, height }
})

onMounted(load)
</script>
