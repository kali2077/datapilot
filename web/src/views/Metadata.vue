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
        <el-table-column prop="row_count" label="行数" width="110" sortable>
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
