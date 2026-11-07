<template>
  <div class="query-layout">
    <!-- ============ 左侧：数据源 / 已保存 / 历史 ============ -->
    <div class="page-card side-panel">
      <div class="side-section">
        <div class="side-title">数据源</div>
        <el-select v-model="dsId" placeholder="选择数据源" filterable style="width: 100%" @change="loadMeta">
          <el-option v-for="d in datasources" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
        </el-select>
        <div v-if="tables.length" class="table-list">
          <div v-for="t in tables" :key="t.id" class="table-item" @click="insertTable(t.name)">
            <el-icon><i-ep-Notebook /></el-icon>
            <span class="mono">{{ t.name }}</span>
            <span class="muted tiny">{{ t.row_count >= 0 ? t.row_count + ' 行' : '' }}</span>
          </div>
        </div>
        <div v-else-if="dsId" class="muted tiny" style="margin-top: 8px">该数据源暂无元数据（可到数据源页执行采集）</div>
      </div>

      <div class="side-section">
        <div class="side-title">
          已保存查询
          <el-button link type="primary" size="small" icon="Plus" @click="openSave">保存当前</el-button>
        </div>
        <div v-if="saved.length" class="table-list">
          <div v-for="s in saved" :key="s.id" class="table-item" @click="applySaved(s)">
            <el-icon><i-ep-Star /></el-icon>
            <span class="ellipsis">{{ s.name }}</span>
            <el-button link type="success" size="small" icon="VideoPlay" title="直接运行"
                       @click.stop="runSaved(s)" />
            <el-button link type="danger" size="small" icon="Delete" @click.stop="removeSaved(s)" />
          </div>
        </div>
        <div v-else class="muted tiny">暂无保存查询</div>
      </div>

      <div class="side-section">
        <div class="side-title">
          查询历史
          <el-button link type="danger" size="small" icon="Delete" @click="clearHistory">清空</el-button>
        </div>
        <div v-if="history.length" class="table-list history-list">
          <div v-for="h in history" :key="h.id" class="table-item" @click="applyHistory(h)">
            <el-icon :color="h.status === 'ok' ? 'var(--dp-ok)' : 'var(--dp-danger)'"><i-ep-Stopwatch /></el-icon>
            <span class="ellipsis mono tiny">{{ h.sql }}</span>
            <span class="muted tiny">{{ h.row_count }}行</span>
          </div>
        </div>
        <div v-else class="muted tiny">暂无查询历史</div>
      </div>
    </div>

    <!-- ============ 右侧：编辑器 + 结果 ============ -->
    <div class="main-col">
      <div class="page-card">
        <div class="toolbar" style="margin-bottom: 8px">
          <span class="page-title" style="font-size: 16px">数据查询</span>
          <span class="muted tiny">只读 SELECT · 自动加 LIMIT · Ctrl/Cmd + Enter 执行</span>
          <div class="spacer" />
          <el-select v-model="rowLimit" style="width: 110px" size="small">
            <el-option v-for="n in [100, 500, 1000]" :key="n" :label="`上限 ${n} 行`" :value="n" />
          </el-select>
          <el-button type="primary" icon="VideoPlay" :loading="running" :disabled="!dsId || !sql.trim()" @click="run">
            运行查询
          </el-button>
        </div>
        <el-input
          v-model="sql"
          type="textarea"
          :rows="7"
          class="sql-editor"
          placeholder="输入只读 SQL，例如：SELECT dt, COUNT(*) AS cnt FROM orders GROUP BY dt ORDER BY dt DESC"
          @keydown.ctrl.enter.prevent="run"
          @keydown.meta.enter.prevent="run"
        />
      </div>

      <div class="page-card" v-loading="running">
        <template v-if="result">
          <div class="toolbar" style="margin-bottom: 8px">
            <span class="status-cell"><span class="dp-dot ok" />成功</span>
            <span class="muted">{{ result.row_count }} 行 · {{ result.cost_ms }} ms</span>
            <el-tag v-if="result.truncated" type="warning" size="small">已达上限，结果截断</el-tag>
            <span class="muted tiny">数据源: {{ result.datasource }}</span>
            <div class="spacer" />
            <el-button size="small" icon="Document" @click="copySql">复制 SQL</el-button>
            <el-button size="small" icon="Download" @click="exportCsv">导出 CSV</el-button>
          </div>
          <el-collapse v-if="result.sql" style="margin-bottom: 8px">
            <el-collapse-item title="实际执行的 SQL" name="sql">
              <pre class="sql-pre">{{ result.sql }}</pre>
            </el-collapse-item>
          </el-collapse>
          <el-table :data="pagedRows" stripe highlight-current-row max-height="520" size="small">
            <el-table-column
              v-for="(c, i) in result.columns" :key="c" :prop="String(i)" :label="c"
              min-width="120" show-overflow-tooltip
            >
              <template #default="{ row }">{{ row[i] === null ? 'NULL' : row[i] }}</template>
            </el-table-column>
            <template #empty><el-empty description="查询结果为空" /></template>
          </el-table>
          <div v-if="result.rows.length > pageSize" style="display: flex; justify-content: flex-end; margin-top: 10px">
            <el-pagination v-model:current-page="page" :page-size="pageSize" :total="result.rows.length"
                           layout="total, prev, pager, next" background />
          </div>
        </template>
        <el-empty v-else description="输入 SQL 并运行查询（仅支持只读 SELECT / WITH / SHOW / EXPLAIN）" />
      </div>
    </div>

    <!-- 保存查询对话框 -->
    <el-dialog v-model="saveVisible" title="保存查询" width="420px">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="saveName" placeholder="例如：每日订单量" maxlength="60" />
        </el-form-item>
        <el-form-item label="SQL">
          <pre class="sql-pre" style="max-height: 120px; overflow: auto; width: 100%">{{ sql || '(空)' }}</pre>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="saveVisible = false">取消</el-button>
        <el-button type="primary" :disabled="!saveName.trim() || !sql.trim()" @click="doSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, computed } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'

const datasources = ref([])
const dsId = ref(null)
const tables = ref([])
const saved = ref([])
const history = ref([])

const sql = ref('')
const rowLimit = ref(500)
const running = ref(false)
const result = ref(null)
const page = ref(1)
const pageSize = 100

const saveVisible = ref(false)
const saveName = ref('')

const pagedRows = computed(() => {
  if (!result.value) return []
  const start = (page.value - 1) * pageSize
  return result.value.rows.slice(start, start + pageSize)
})

async function loadDatasources() {
  datasources.value = await http.get('/api/datasources')
  if (!dsId.value && datasources.value.length) {
    dsId.value = datasources.value[0].id
    loadMeta()
  }
}

async function loadMeta() {
  tables.value = []
  if (!dsId.value) return
  try {
    const d = await http.get('/api/meta/tables', { params: { datasource_id: dsId.value, size: 100 } })
    tables.value = d.items || []
  } catch (e) { /* 元数据缺失不阻塞查询 */ }
}

async function loadSaved() {
  saved.value = await http.get('/api/query/saved')
}

async function loadHistory() {
  history.value = await http.get('/api/query/history', { params: { limit: 30 } })
}

async function run() {
  if (!dsId.value || !sql.value.trim() || running.value) return
  running.value = true
  page.value = 1
  try {
    result.value = await http.post('/api/query/run', {
      datasource_id: dsId.value, sql: sql.value, limit: rowLimit.value,
    })
    loadHistory()
  } catch (e) {
    // 错误已由拦截器提示；刷新历史（含失败记录）
    loadHistory()
  } finally {
    running.value = false
  }
}

function insertTable(name) {
  sql.value = (sql.value ? sql.value.replace(/\s+$/, '') + '\n' : '') + `SELECT * FROM ${name} LIMIT 20`
}

function applySaved(s) {
  dsId.value = s.datasource_id
  sql.value = s.sql
  loadMeta()
}

async function runSaved(s) {
  dsId.value = s.datasource_id
  sql.value = s.sql
  loadMeta()
  await nextTick()
  run()
}

function applyHistory(h) {
  dsId.value = h.datasource_id || dsId.value
  sql.value = h.sql
  loadMeta()
}

async function removeSaved(s) {
  await http.delete(`/api/query/saved/${s.id}`)
  ElMessage.success('已删除')
  loadSaved()
}

async function clearHistory() {
  await ElMessageBox.confirm('确定清空全部查询历史？', '提示', { type: 'warning' })
  await http.delete('/api/query/history')
  loadHistory()
}

function openSave() {
  if (!sql.value.trim()) { ElMessage.warning('请先输入 SQL'); return }
  saveName.value = ''
  saveVisible.value = true
}

async function doSave() {
  try {
    await http.post('/api/query/saved', { name: saveName.value, datasource_id: dsId.value, sql: sql.value })
    saveVisible.value = false
    ElMessage.success('已保存')
    loadSaved()
  } catch (e) { /* 拦截器已提示 */ }
}

function copySql() {
  navigator.clipboard?.writeText(result.value?.sql || sql.value)
  ElMessage.success('SQL 已复制')
}

function exportCsv() {
  const r = result.value
  if (!r) return
  const esc = (v) => {
    const s = v === null || v === undefined ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const lines = [r.columns.map(esc).join(',')]
  for (const row of r.rows) lines.push(row.map(esc).join(','))
  const blob = new Blob(['\ufeff' + lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `query_result_${Date.now()}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

onMounted(() => {
  loadDatasources()
  loadSaved()
  loadHistory()
})
</script>

<style scoped>
.status-cell {
  display: inline-flex;
  align-items: center;
  font-size: 12.5px;
  color: var(--dp-text-2);
}
.query-layout {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.side-panel {
  width: 250px;
  flex-shrink: 0;
}
.side-section + .side-section {
  margin-top: 16px;
}
.side-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.table-list {
  max-height: 200px;
  overflow: auto;
  margin-top: 6px;
}
.history-list {
  max-height: 260px;
}
.table-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 12px;
}
.table-item:hover {
  background: var(--dp-accent-soft);
}
.table-item .ellipsis {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.main-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sql-editor :deep(textarea) {
  font-family: var(--dp-mono);
  font-size: 13px;
}
</style>
