<template>
  <div>
    <div class="page-card">
      <div class="toolbar">
        <el-select v-model="q.datasource_id" placeholder="全部数据源" clearable style="width: 200px" @change="search(1)">
          <el-option v-for="d in dsList" :key="d.id" :label="d.name" :value="d.id" />
        </el-select>
        <el-input
          v-model="q.keyword" placeholder="搜索表名 / 注释 / 描述" clearable style="width: 260px"
          :prefix-icon="'Search'" @keyup.enter="search(1)" @clear="search(1)"
        />
        <el-button type="primary" icon="Search" @click="search(1)">搜索</el-button>
        <div class="spacer" />
        <el-button icon="Warning" @click="openChanges">变更记录</el-button>
        <span class="muted">共 {{ total }} 张表</span>
      </div>

      <el-table v-loading="loading" :data="items" stripe highlight-current-row @row-click="openDrawer">
        <el-table-column prop="name" label="表名" min-width="170">
          <template #default="{ row }"><span class="mono" style="font-weight: 600">{{ row.name }}</span></template>
        </el-table-column>
        <el-table-column prop="datasource" label="数据源" width="130" />
        <el-table-column label="注释" min-width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ row.comment || row.manual_desc || '-' }}</template>
        </el-table-column>
        <el-table-column prop="manual_desc" label="手动描述" min-width="150" show-overflow-tooltip>
          <template #default="{ row }">{{ row.manual_desc || '-' }}</template>
        </el-table-column>
        <el-table-column prop="owner_name" label="负责人" width="110">
          <template #default="{ row }">{{ row.owner_name || '-' }}</template>
        </el-table-column>
        <el-table-column prop="row_count" label="行数" width="110" sortable align="right">
          <template #default="{ row }">{{ row.row_count ?? '-' }}</template>
        </el-table-column>
        <el-table-column prop="last_collected_at" label="采集时间" width="170">
          <template #default="{ row }"><span class="muted">{{ row.last_collected_at || '未采集' }}</span></template>
        </el-table-column>
        <template #empty><el-empty description="暂无元数据，请先在数据源页面执行采集" /></template>
      </el-table>
      <div style="display: flex; justify-content: flex-end; margin-top: 12px">
        <el-pagination
          v-model:current-page="page" :page-size="size" :total="total" layout="total, prev, pager, next"
          background @current-change="search()"
        />
      </div>
    </div>

    <!-- 表详情抽屉 -->
    <el-drawer v-model="drawer.visible" :title="`表详情：${drawer.row?.name || ''}`" size="640px">
      <template v-if="drawer.detail">
        <el-descriptions :column="2" size="small" border style="margin-bottom: 16px">
          <el-descriptions-item label="表名"><span class="mono">{{ drawer.detail.name }}</span></el-descriptions-item>
          <el-descriptions-item label="数据源">{{ drawer.detail.datasource }}</el-descriptions-item>
          <el-descriptions-item label="行数">{{ drawer.detail.row_count ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="系统注释">{{ drawer.detail.comment || '-' }}</el-descriptions-item>
        </el-descriptions>

        <div v-if="auth.canWrite" style="margin-bottom: 14px">
          <el-form label-width="70px" size="small">
            <el-form-item label="负责人">
              <el-input v-model="drawer.ownerName" placeholder="表负责人" style="width: 220px" />
            </el-form-item>
            <el-form-item label="手动描述">
              <el-input v-model="drawer.manualDesc" type="textarea" :rows="2" placeholder="业务口径等补充说明" />
            </el-form-item>
          </el-form>
          <el-button type="primary" size="small" :loading="drawer.saving" @click="saveTableMeta">保存修改</el-button>
        </div>
        <div v-else style="margin-bottom: 12px" class="muted">
          负责人：{{ drawer.detail.owner_name || '-' }} ｜ 手动描述：{{ drawer.detail.manual_desc || '-' }}
        </div>

        <h4 style="margin: 6px 0 8px">字段列表（{{ drawer.detail.columns?.length || 0 }}）</h4>
        <el-table :data="drawer.detail.columns || []" size="small" border max-height="480">
          <el-table-column prop="name" label="字段" min-width="130">
            <template #default="{ row }">
              <span class="mono">{{ row.name }}</span>
              <el-tag v-if="row.is_pk" type="warning" size="small" style="margin-left: 4px">PK</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="类型" width="120">
            <template #default="{ row }"><span class="mono muted">{{ row.type }}</span></template>
          </el-table-column>
          <el-table-column label="注释" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">{{ row.manual_desc || row.comment || '-' }}</template>
          </el-table-column>
          <el-table-column label="样例值" min-width="150" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="muted">{{ (row.samples || []).join(', ') || '-' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <div v-else v-loading="true" style="height: 300px" />
    </el-drawer>

    <!-- 资产变更巡检抽屉（数据源级） -->
    <el-drawer v-model="chgDlg.visible" :title="`资产变更巡检${chgDlg.dsName ? '：' + chgDlg.dsName : '（全部数据源）'}`" size="640px">
      <div style="display: flex; gap: 8px; margin-bottom: 12px">
        <el-select v-model="chgDlg.type" placeholder="全部类型" clearable style="width: 150px" @change="loadChanges">
          <el-option v-for="t in changeTypes" :key="t.v" :label="t.l" :value="t.v" />
        </el-select>
        <div class="spacer" />
        <span class="muted">共 {{ chgTotal }} 条</span>
      </div>
      <el-table v-loading="chgDlg.loading" :data="chgItems" size="small" border @row-click="(row) => row.table_id && $router.push(`/assets/${row.table_id}`)">
        <el-table-column prop="detected_at" label="发现时间" width="165" />
        <el-table-column label="类型" width="105">
          <template #default="{ row }">
            <el-tag :type="row.danger ? 'danger' : 'info'" size="small" effect="plain">{{ chgLabel(row.change_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="table_name" label="表" width="150" class-name="mono" show-overflow-tooltip />
        <el-table-column label="详情" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ chgDetail(row) }}</template>
        </el-table-column>
        <template #empty><el-empty description="暂无变更记录（采集时自动对比上次快照）" /></template>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import http, { errMsg } from '../api'
import { useAuthStore } from '../store'

const auth = useAuthStore()

const dsList = ref([])
const items = ref([])
const total = ref(0)
const page = ref(1)
const size = 50
const loading = ref(false)
const q = reactive({ datasource_id: null, keyword: '' })

const drawer = reactive({ visible: false, row: null, detail: null, ownerName: '', manualDesc: '', saving: false })

/* ---------- 资产变更巡检 ---------- */
import { useRouter } from 'vue-router'
const router = useRouter()
const chgItems = ref([])
const chgTotal = ref(0)
const chgDlg = reactive({ visible: false, loading: false, type: '' })
const changeTypes = [
  { v: 'table_added', l: '新增表' },
  { v: 'table_removed', l: '删除表' },
  { v: 'column_added', l: '新增字段' },
  { v: 'column_removed', l: '删除字段' },
  { v: 'column_altered', l: '字段变更' },
  { v: 'comment_changed', l: '注释变更' },
  { v: 'row_count_changed', l: '行数变化' },
]

function chgLabel(t) {
  return (changeTypes.find((x) => x.v === t) || {}).l || t
}

function chgDetail(row) {
  const d = row.detail || {}
  switch (row.change_type) {
    case 'table_added': return `表被创建进入资产${d.recreated ? '（同名表重建）' : ''}`
    case 'table_removed': return `表从数据源中被删除${d.last_row_count >= 0 ? `（最后行数 ${d.last_row_count}）` : ''}`
    case 'column_added': return `新增字段 ${d.column}${d.data_type ? ` (${d.data_type})` : ''}`
    case 'column_removed': return `字段 ${d.column} 被删除${d.old_type ? `（原类型 ${d.old_type}）` : ''}`
    case 'column_altered': return `字段 ${d.column}：${d.old_type || '-'} → ${d.new_type || '-'}${d.old_pk !== d.new_pk ? `，主键 ${d.old_pk} → ${d.new_pk}` : ''}`
    case 'comment_changed': return `表注释变更：${d.old || '(空)'} → ${d.new || '(空)'}`
    case 'row_count_changed': return `行数 ${d.old} → ${d.new}（${d.pct > 0 ? '+' : ''}${d.pct}%）`
    default: return JSON.stringify(d)
  }
}

async function openChanges() {
  chgDlg.visible = true
  await loadChanges()
}

async function loadChanges() {
  chgDlg.loading = true
  try {
    const res = await http.get('/api/meta/changes', {
      params: { datasource_id: q.datasource_id || 0, change_type: chgDlg.type || '', size: 100 },
    })
    chgItems.value = res.items
    chgTotal.value = res.total
  } catch { /* ignore */ } finally {
    chgDlg.loading = false
  }
}

async function loadDs() {
  try {
    dsList.value = await http.get('/api/datasources')
  } catch { /* ignore */ }
}

async function search(p) {
  if (p) page.value = p
  loading.value = true
  try {
    const res = await http.get('/api/meta/tables', {
      params: {
        datasource_id: q.datasource_id || 0,
        keyword: q.keyword || '',
        page: page.value,
        size,
      },
    })
    items.value = res.items
    total.value = res.total
  } catch { /* ignore */ } finally {
    loading.value = false
  }
}

async function openDrawer(row) {
  drawer.row = row
  drawer.detail = null
  drawer.visible = true
  try {
    const detail = await http.get(`/api/meta/tables/${row.id}`)
    drawer.detail = detail
    drawer.ownerName = detail.owner_name || ''
    drawer.manualDesc = detail.manual_desc || ''
  } catch { /* ignore */ }
}

async function saveTableMeta() {
  drawer.saving = true
  try {
    await http.put(`/api/meta/tables/${drawer.detail.id}`, {
      manual_desc: drawer.manualDesc,
      owner_name: drawer.ownerName,
    })
    ElMessage.success('已保存')
    drawer.detail.manual_desc = drawer.manualDesc
    drawer.detail.owner_name = drawer.ownerName
    search()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    drawer.saving = false
  }
}

onMounted(() => {
  loadDs()
  search(1)
})
</script>
