<template>
  <div class="page-card">
    <div class="toolbar">
      <h3 class="page-title" style="margin: 0"><el-icon><Document /></el-icon>审计日志</h3>
      <el-input v-model="q.user" placeholder="用户名" clearable style="width: 160px" @keyup.enter="search(1)" @clear="search(1)" />
      <el-input v-model="q.action" placeholder="动作（如 login / chat.send）" clearable style="width: 200px" @keyup.enter="search(1)" @clear="search(1)" />
      <el-select v-model="q.source" placeholder="全部来源" clearable style="width: 130px" @change="search(1)">
        <el-option label="UI 界面" value="ui" />
        <el-option label="API 调用" value="api" />
        <el-option label="AI 问数" value="ai" />
      </el-select>
      <el-button type="primary" icon="Search" @click="search(1)">查询</el-button>
      <div class="spacer" />
      <span class="muted">共 {{ total }} 条</span>
      <el-button icon="Refresh" circle @click="search()" />
    </div>

    <el-table v-loading="loading" :data="items" stripe>
      <el-table-column prop="ts" label="时间" width="175" />
      <el-table-column prop="username" label="用户" width="110" />
      <el-table-column prop="action" label="动作" width="160">
        <template #default="{ row }">
          <el-tag size="small" effect="plain" class="mono">{{ row.action }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="资源" width="150">
        <template #default="{ row }">
          <span v-if="row.resource_type">{{ row.resource_type }} #{{ row.resource_id ?? '-' }}</span>
          <span v-else class="muted">-</span>
        </template>
      </el-table-column>
      <el-table-column label="详情" min-width="240" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="mono muted">{{ detailText(row.detail) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="80">
        <template #default="{ row }">
          <el-tag size="small" :type="sourceTag(row.source)">{{ row.source }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="ip" label="IP" width="130">
        <template #default="{ row }"><span class="mono muted">{{ row.ip || '-' }}</span></template>
      </el-table-column>
      <template #empty><el-empty description="暂无日志" /></template>
    </el-table>
    <div style="display: flex; justify-content: flex-end; margin-top: 12px">
      <el-pagination
        v-model:current-page="page" :page-size="size" :total="total"
        layout="total, prev, pager, next, sizes" :page-sizes="[20, 50, 100]" background
        @current-change="search()" @size-change="search(1)"
      />
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import http from '../api'

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(50)
const loading = ref(false)
const q = reactive({ user: '', action: '', source: '' })

function detailText(d) {
  if (d === null || d === undefined || d === '') return '-'
  if (typeof d === 'string') return d
  return JSON.stringify(d)
}

function sourceTag(s) {
  return { ui: 'primary', api: 'warning', ai: 'success' }[s] || 'info'
}

async function search(p) {
  if (p) page.value = p
  loading.value = true
  try {
    const res = await http.get('/api/audit/logs', {
      params: { user: q.user || '', action: q.action || '', source: q.source || '', page: page.value, size: size.value },
    })
    items.value = res.items
    total.value = res.total
  } catch { /* ignore */ } finally {
    loading.value = false
  }
}

onMounted(() => search(1))
</script>
