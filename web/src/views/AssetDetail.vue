<template>
  <div>
    <div class="page-card" style="margin-bottom: 14px">
      <div style="display: flex; align-items: flex-start; gap: 14px; flex-wrap: wrap">
        <el-button icon="ArrowLeft" circle @click="$router.back()" />
        <div style="flex: 1; min-width: 260px">
          <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
            <span class="mono" style="font-size: 20px; font-weight: 700">{{ detail?.name || '...' }}</span>
            <el-tag v-if="detail?.datasource" effect="plain">{{ detail.datasource.name }} · {{ detail.datasource.type }}</el-tag>
            <el-tag
              v-for="t in detail?.tags || []" :key="t.id" size="small"
              :color="t.color" effect="dark" disable-transitions
            >{{ t.name }}</el-tag>
          </div>
          <div class="muted" style="margin-top: 6px">
            {{ detail?.comment || detail?.manual_desc || '暂无注释' }}
            ｜ 负责人: {{ detail?.owner_name || '-' }}
            ｜ 行数: {{ detail?.row_count ?? '-' }}
          </div>
        </div>
        <div style="display: flex; gap: 8px; align-items: center">
          <template v-if="auth.canWrite && detail">
            <el-select
              v-model="selectedTags" multiple filterable placeholder="管理标签" style="width: 260px"
              :loading="tagSaving" @change="onTagsChange"
            >
              <el-option v-for="t in allTags" :key="t.id" :label="t.name" :value="t.id">
                <span :style="{ display: 'inline-block', width: '9px', height: '9px', borderRadius: '2px', background: t.color, marginRight: '6px' }" />
                {{ t.name }}
              </el-option>
            </el-select>
            <el-button icon="Plus" circle @click="newTagDlg.visible = true" />
          </template>
          <el-button
            v-if="detail" icon="ChatDotRound"
            @click="openAi(`介绍一下表 ${detail.name}：字段含义、上下游血缘，以及能怎么用`)"
          >问 AI</el-button>
        </div>
      </div>
    </div>

    <div class="page-card">
      <el-tabs v-model="tab">
        <el-tab-pane label="字段信息" name="columns">
          <el-table v-if="detail" :data="detail.columns || []" stripe size="small" border>
            <el-table-column prop="name" label="字段" min-width="150">
              <template #default="{ row }">
                <span class="mono">{{ row.name }}</span>
                <el-tag v-if="row.is_pk" type="warning" size="small" style="margin-left: 4px">PK</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="type" label="类型" width="130">
              <template #default="{ row }"><span class="mono muted">{{ row.type }}</span></template>
            </el-table-column>
            <el-table-column label="注释" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">{{ row.comment || '-' }}</template>
            </el-table-column>
            <el-table-column label="样例值" min-width="180" show-overflow-tooltip>
              <template #default="{ row }">{{ (row.samples || []).join(', ') || '-' }}</template>
            </el-table-column>
            <template #empty><el-empty description="暂无字段信息" :image-size="70" /></template>
          </el-table>
          <div v-else v-loading="true" style="height: 260px" />
        </el-tab-pane>

        <el-tab-pane label="样例数据" name="sample">
          <el-alert
            v-if="detail?.sample?.error" type="error" :closable="false" show-icon
            :title="`样例数据读取失败：${detail.sample.error}`"
          />
          <template v-else-if="detail?.sample">
            <el-table :data="sampleRows" stripe size="small" border max-height="440">
              <el-table-column
                v-for="(c, i) in detail.sample.columns || []" :key="i" :label="String(c)" :prop="'c' + i"
                min-width="140" show-overflow-tooltip
              />
            </el-table>
            <div class="muted" style="margin-top: 8px">前 {{ sampleRows.length }} 行样例（LIMIT 10）</div>
          </template>
          <el-empty v-else description="暂无样例数据" :image-size="70" />
        </el-tab-pane>

        <el-tab-pane label="血缘关系" name="lineage">
          <LineageGraph
            v-if="detail?.datasource?.id" :datasource-id="detail.datasource.id" :table="detail.name"
            style="margin-bottom: 16px"
          />
          <el-row v-if="detail" :gutter="14">
            <el-col :span="12">
              <h4 style="margin: 4px 0 8px"><el-tag type="success" size="small" effect="plain">上游</el-tag> {{ detail.upstream?.length || 0 }} 张表</h4>
              <el-table :data="detail.upstream || []" size="small" border>
                <el-table-column prop="table" label="表" min-width="140" class-name="mono" />
                <el-table-column prop="datasource" label="数据源" width="110" />
                <el-table-column prop="job_type" label="作业类型" width="90" />
                <el-table-column label="工作流" width="90">
                  <template #default="{ row }">
                    <el-link v-if="row.workflow_id" type="primary" @click="$router.push(`/ops?workflow_id=${row.workflow_id}`)">#{{ row.workflow_id }}</el-link>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <template #empty><el-empty description="无上游依赖" :image-size="50" /></template>
              </el-table>
            </el-col>
            <el-col :span="12">
              <h4 style="margin: 4px 0 8px"><el-tag type="warning" size="small" effect="plain">下游</el-tag> {{ detail.downstream?.length || 0 }} 张表</h4>
              <el-table :data="detail.downstream || []" size="small" border>
                <el-table-column prop="table" label="表" min-width="140" class-name="mono" />
                <el-table-column prop="datasource" label="数据源" width="110" />
                <el-table-column prop="job_type" label="作业类型" width="90" />
                <el-table-column label="工作流" width="90">
                  <template #default="{ row }">
                    <el-link v-if="row.workflow_id" type="primary" @click="$router.push(`/ops?workflow_id=${row.workflow_id}`)">#{{ row.workflow_id }}</el-link>
                    <span v-else>-</span>
                  </template>
                </el-table-column>
                <template #empty><el-empty description="无下游依赖" :image-size="50" /></template>
              </el-table>
            </el-col>
          </el-row>
        </el-tab-pane>

        <el-tab-pane label="相关工作流" name="workflows">
          <el-table :data="detail?.related_workflows || []" stripe>
            <el-table-column prop="id" label="ID" width="70" />
            <el-table-column prop="name" label="工作流" min-width="200" />
            <el-table-column label="类型" width="100">
              <template #default="{ row }">
                <el-tag :type="row.type === 'sync' ? 'primary' : 'warning'" size="small">{{ row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="cron" label="Cron" width="150" class-name="mono">
              <template #default="{ row }">{{ row.cron || '手动' }}</template>
            </el-table-column>
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <el-tag :type="row.enabled ? 'success' : 'info'" size="small">{{ row.enabled ? '启用' : '停用' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="110">
              <template #default="{ row }">
                <el-button link type="primary" @click="$router.push(`/ops?workflow_id=${row.id}`)">查看实例</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无相关工作流" :image-size="70" /></template>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 新建标签 -->
    <el-dialog v-model="newTagDlg.visible" title="新建标签" width="400px">
      <div style="display: flex; gap: 8px">
        <el-input v-model="newTagDlg.name" placeholder="标签名称" style="flex: 1" />
        <el-color-picker v-model="newTagDlg.color" />
      </div>
      <template #footer>
        <el-button @click="newTagDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="newTagDlg.saving" @click="createTag">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import http, { errMsg } from '../api'
import { openAi } from '../ai'
import { useAuthStore } from '../store'
import LineageGraph from '../components/LineageGraph.vue'

const route = useRoute()
const auth = useAuthStore()
const tableId = Number(route.params.id)

const detail = ref(null)
const tab = ref('columns')
const allTags = ref([])
const selectedTags = ref([])
const tagSaving = ref(false)
const newTagDlg = reactive({ visible: false, name: '', color: '#409EFF', saving: false })

const sampleRows = computed(() => {
  const s = detail.value?.sample
  if (!s || s.error || !s.rows) return []
  return s.rows.map((r) => Object.fromEntries((r || []).map((v, i) => [`c${i}`, v])))
})

async function load() {
  try {
    detail.value = await http.get(`/api/asset/tables/${tableId}`)
    selectedTags.value = (detail.value.tags || []).map((t) => t.id)
  } catch { /* ignore */ }
}

async function loadAllTags() {
  try {
    allTags.value = await http.get('/api/asset/tags')
  } catch { /* ignore */ }
}

async function onTagsChange(newIds) {
  tagSaving.value = true
  const before = new Set((detail.value.tags || []).map((t) => t.id))
  const after = new Set(newIds)
  try {
    const ops = []
    for (const id of newIds) if (!before.has(id)) ops.push(http.post(`/api/asset/tables/${tableId}/tags`, { tag_id: id, on: true }))
    for (const id of before) if (!after.has(id)) ops.push(http.post(`/api/asset/tables/${tableId}/tags`, { tag_id: id, on: false }))
    await Promise.all(ops)
    ElMessage.success('标签已更新')
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    tagSaving.value = false
  }
}

async function createTag() {
  if (!newTagDlg.name.trim()) {
    ElMessage.warning('请输入标签名称')
    return
  }
  newTagDlg.saving = true
  try {
    await http.post('/api/asset/tags', { name: newTagDlg.name.trim(), color: newTagDlg.color || '#409EFF' })
    newTagDlg.visible = false
    newTagDlg.name = ''
    await loadAllTags()
    ElMessage.success('标签已创建，可在上方选择挂到当前表')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    newTagDlg.saving = false
  }
}

onMounted(() => {
  load()
  loadAllTags()
})
</script>
