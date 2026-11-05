<template>
  <div>
    <!-- 搜索头 -->
    <div class="page-card" style="margin-bottom: 14px">
      <div style="display: flex; align-items: center; gap: 14px">
        <div class="logo-icon" style="width: 40px; height: 40px; border-radius: 10px; background: linear-gradient(135deg,#2563eb,#7c3aed); color:#fff; font-weight:700; font-size:20px; display:flex; align-items:center; justify-content:center; flex:none">
          <el-icon :size="22"><MapLocation /></el-icon>
        </div>
        <div style="flex: 1">
          <div style="font-weight: 700; font-size: 16px">数据资产地图</div>
          <div class="muted">搜索平台内的表、标签与已发布的数据 API</div>
        </div>
        <el-button v-if="auth.canWrite" icon="PriceTag" @click="tagDlg.visible = true">标签管理</el-button>
      </div>
      <div style="display: flex; gap: 10px; margin-top: 14px">
        <el-input
          v-model="keyword" size="large" clearable placeholder="输入表名 / 注释 / 标签关键词，回车搜索"
          :prefix-icon="'Search'" @keyup.enter="doSearch" @clear="doSearch"
        />
        <el-button size="large" type="primary" icon="Search" :loading="loading" @click="doSearch">搜索资产</el-button>
      </div>
    </div>

    <!-- 表结果 -->
    <div class="page-card" style="margin-bottom: 14px">
      <h3 class="page-title"><el-icon><Grid /></el-icon>数据表（{{ tables.length }}）</h3>
      <el-table v-loading="loading" :data="tables" stripe highlight-current-row @row-click="gotoTable">
        <el-table-column prop="name" label="表名" min-width="200">
          <template #default="{ row }">
            <el-link type="primary" class="mono" @click.stop="gotoTable(row)">{{ row.name }}</el-link>
          </template>
        </el-table-column>
        <el-table-column prop="datasource" label="数据源" width="140" />
        <el-table-column label="注释" min-width="220" show-overflow-tooltip>
          <template #default="{ row }">{{ row.comment || '-' }}</template>
        </el-table-column>
        <el-table-column prop="owner" label="负责人" width="110">
          <template #default="{ row }">{{ row.owner || '-' }}</template>
        </el-table-column>
        <el-table-column prop="rows" label="行数" width="120" sortable>
          <template #default="{ row }">{{ row.rows ?? '-' }}</template>
        </el-table-column>
        <el-table-column label="标签" min-width="200">
          <template #default="{ row }">
            <template v-if="row.tags && row.tags.length">
              <el-tag
                v-for="t in row.tags" :key="t.id" size="small" style="margin-right: 6px"
                :color="t.color" effect="dark" disable-transitions
              >{{ t.name }}</el-tag>
            </template>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="!keyword ? '输入关键词搜索数据表（平台按元数据索引匹配）' : '未找到匹配的表'" />
        </template>
      </el-table>
    </div>

    <!-- API 结果 -->
    <div class="page-card" v-if="apis.length">
      <h3 class="page-title"><el-icon><Link /></el-icon>数据 API（{{ apis.length }}）</h3>
      <el-table :data="apis" stripe>
        <el-table-column prop="code" label="编码" min-width="160" class-name="mono" />
        <el-table-column prop="name" label="名称" min-width="220" />
        <el-table-column label="操作" width="120">
          <template #default>
            <el-button link type="primary" @click="$router.push('/apis')">前往数据服务</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 标签管理 -->
    <el-dialog v-model="tagDlg.visible" title="标签管理" width="480px">
      <div style="display: flex; gap: 8px; margin-bottom: 12px">
        <el-input v-model="tagDlg.name" placeholder="新标签名称" style="flex: 1" @keyup.enter="addTag" />
        <el-color-picker v-model="tagDlg.color" />
        <el-button type="primary" :loading="tagDlg.saving" @click="addTag">添加</el-button>
      </div>
      <el-table :data="tags" size="small">
        <el-table-column label="标签" min-width="140">
          <template #default="{ row }">
            <el-tag :color="row.color" effect="dark" disable-transitions>{{ row.name }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="90">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeTag(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无标签" :image-size="60" /></template>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import { useAuthStore } from '../store'

const router = useRouter()
const auth = useAuthStore()
const keyword = ref('')
const tables = ref([])
const apis = ref([])
const loading = ref(false)
const searched = ref(false)

const tags = ref([])
const tagDlg = reactive({ visible: false, name: '', color: '#409EFF', saving: false })

async function doSearch() {
  loading.value = true
  searched.value = true
  try {
    const res = await http.get('/api/asset/search', { params: { keyword: keyword.value || '' } })
    tables.value = res.tables || []
    apis.value = res.apis || []
  } catch { /* ignore */ } finally {
    loading.value = false
  }
}

function gotoTable(row) {
  router.push(`/assets/${row.id}`)
}

async function loadTags() {
  try {
    tags.value = await http.get('/api/asset/tags')
  } catch { /* ignore */ }
}

async function addTag() {
  if (!tagDlg.name.trim()) {
    ElMessage.warning('请输入标签名')
    return
  }
  tagDlg.saving = true
  try {
    await http.post('/api/asset/tags', { name: tagDlg.name.trim(), color: tagDlg.color || '#409EFF' })
    ElMessage.success('标签已创建')
    tagDlg.name = ''
    loadTags()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    tagDlg.saving = false
  }
}

async function removeTag(row) {
  try {
    await ElMessageBox.confirm(`删除标签「${row.name}」？与表的关联将一并移除。`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/asset/tags/${row.id}`)
    ElMessage.success('已删除')
    loadTags()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(() => {
  doSearch()
  loadTags()
})
</script>
