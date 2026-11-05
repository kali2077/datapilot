<template>
  <div>
    <!-- ============ 概览卡片 ============ -->
    <el-row :gutter="12" style="margin-bottom: 12px">
      <el-col :span="4"><div class="page-card stat"><div class="stat-num">{{ sum.rules_total }}</div><div class="stat-label">监控规则</div></div></el-col>
      <el-col :span="4"><div class="page-card stat"><div class="stat-num">{{ sum.rules_enabled }}</div><div class="stat-label">启用中</div></div></el-col>
      <el-col :span="4"><div class="page-card stat"><div class="stat-num">{{ sum.checks_24h }}</div><div class="stat-label">24h 检查</div></div></el-col>
      <el-col :span="4"><div class="page-card stat"><div class="stat-num" :class="{ 'stat-bad': sum.failed_24h > 0 }">{{ sum.failed_24h }}</div><div class="stat-label">24h 失败</div></div></el-col>
      <el-col :span="4"><div class="page-card stat"><div class="stat-num">{{ sum.pass_rate === null ? '-' : sum.pass_rate + '%' }}</div><div class="stat-label">24h 通过率</div></div></el-col>
      <el-col :span="4"><div class="page-card stat"><div class="stat-num" :class="{ 'stat-bad': sum.alerts_24h > 0 }">{{ sum.alerts_24h }}</div><div class="stat-label">24h 监控告警</div></div></el-col>
    </el-row>

    <el-alert v-if="sum.failing_rules && sum.failing_rules.length" type="error" show-icon :closable="false" style="margin-bottom: 12px">
      <template #title>
        当前异常规则：
        <el-tag v-for="f in sum.failing_rules" :key="f.id" size="small" :type="f.severity === 'critical' ? 'danger' : 'warning'" style="margin-right: 6px">
          {{ f.name }}（{{ f.message?.slice(0, 40) }}）
        </el-tag>
      </template>
    </el-alert>

    <div class="page-card">
      <el-tabs v-model="tab" @tab-change="onTab">
        <!-- ============ 监控规则 ============ -->
        <el-tab-pane label="监控规则" name="rules">
          <div class="toolbar">
            <span class="muted">数据质量 / 任务健康定时巡检，失败经告警通道通知（通道在调度运维-告警通道配置）</span>
            <div class="spacer" />
            <el-select v-model="groupFilter" placeholder="全部分组" clearable style="width: 150px">
              <el-option v-for="g in groups" :key="g" :label="g" :value="g" />
            </el-select>
            <el-button icon="ChatDotRound" @click="openAi('看看监控情况，如有异常规则给出处理建议')">AI 查监控</el-button>
            <el-button type="primary" icon="Plus" @click="openDlg()">新建规则</el-button>
            <el-button icon="Refresh" circle @click="loadRules" />
          </div>
          <el-table :data="filteredRules" stripe v-loading="loading">
            <el-table-column prop="name" label="规则名称" min-width="150" />
            <el-table-column label="类型" width="120">
              <template #default="{ row }">
                <el-tag size="small" effect="plain">{{ typeLabel(row.rule_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="分组" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.group_name" size="small" type="info" effect="plain">{{ row.group_name }}</el-tag>
                <span v-else class="muted">-</span>
              </template>
            </el-table-column>
            <el-table-column label="监控对象" min-width="180">
              <template #default="{ row }">
                <span class="mono tiny">
                  {{ row.rule_type === 'task_duration' ? ('工作流 ' + (row.params?.workflow_name || '-'))
                     : ((row.datasource || '-') + ' · ' + row.table_name + (row.column_name ? '.' + row.column_name : '')) }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="cron" label="调度" width="100" />
            <el-table-column label="严重度" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.severity === 'critical' ? 'danger' : 'warning'">
                  {{ row.severity === 'critical' ? '严重' : '警告' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最近状态" width="110">
              <template #default="{ row }">
                <el-tag v-if="row.last_status" size="small" effect="dark" :type="statusTag(row.last_status)">
                  {{ statusLabel(row.last_status) }}
                </el-tag>
                <span v-else class="muted">未检查</span>
              </template>
            </el-table-column>
            <el-table-column prop="last_checked_at" label="最近检查" width="160">
              <template #default="{ row }"><span class="muted tiny">{{ row.last_checked_at || '-' }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="250" fixed="right">
              <template #default="{ row }">
                <el-button link type="success" @click="runNow(row)">立即检查</el-button>
                <el-button link :type="row.enabled ? 'warning' : 'success'" @click="toggle(row)">
                  {{ row.enabled ? '停用' : '启用' }}
                </el-button>
                <el-button link type="primary" @click="openDlg(row)">编辑</el-button>
                <el-button link type="primary" @click="$router.push(`/monitoring?rule_id=${row.id}`); tab = 'checks'">记录</el-button>
                <el-button link type="danger" @click="removeRule(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无监控规则" /></template>
          </el-table>
        </el-tab-pane>

        <!-- ============ 检查记录 ============ -->
        <el-tab-pane label="检查记录" name="checks">
          <div class="toolbar">
            <span class="muted">近 7 天检查趋势</span>
          </div>
          <div ref="trendEl" style="width: 100%; height: 220px; margin-bottom: 8px" />
          <div class="toolbar">
            <el-select v-model="checkQ.rule_id" placeholder="全部规则" clearable filterable style="width: 220px" @change="loadChecks(1)">
              <el-option v-for="r in rules" :key="r.id" :label="r.name" :value="r.id" />
            </el-select>
            <el-select v-model="checkQ.status" placeholder="全部状态" clearable style="width: 130px" @change="loadChecks(1)">
              <el-option label="通过" value="passed" />
              <el-option label="失败" value="failed" />
              <el-option label="异常" value="error" />
            </el-select>
            <div class="spacer" />
            <span class="muted">共 {{ checkTotal }} 条</span>
            <el-button icon="Refresh" circle @click="loadChecks()" />
          </div>
          <el-table :data="checks" stripe v-loading="checkLoading" size="small">
            <el-table-column prop="ts" label="时间" width="170" />
            <el-table-column prop="rule_name" label="规则" min-width="140" />
            <el-table-column label="类型" width="110">
              <template #default="{ row }">{{ typeLabel(row.rule_type) }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" effect="dark" :type="statusTag(row.status)">{{ statusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="metric" label="指标" min-width="170" show-overflow-tooltip />
            <el-table-column prop="message" label="说明" min-width="220" show-overflow-tooltip />
            <el-table-column prop="cost_ms" label="耗时" width="80">
              <template #default="{ row }">{{ row.cost_ms }} ms</template>
            </el-table-column>
            <template #empty><el-empty description="暂无检查记录" /></template>
          </el-table>
          <div style="display: flex; justify-content: flex-end; margin-top: 12px">
            <el-pagination v-model:current-page="checkPage" :page-size="checkSize" :total="checkTotal"
                           layout="total, prev, pager, next" background @current-change="loadChecks()" />
          </div>
        </el-tab-pane>

        <!-- ============ 主机状态 ============ -->
        <el-tab-pane label="主机状态" name="host">
          <div class="toolbar">
            <span class="muted">平台所在主机实时资源（10s 自动刷新）</span>
            <div class="spacer" />
            <el-switch v-model="hostAuto" active-text="自动刷新" />
            <el-button icon="Refresh" circle @click="loadHost" />
          </div>
          <el-row v-if="host" :gutter="12">
            <el-col :span="6"><div class="page-card stat"><div class="stat-num">{{ host.cpu_percent }}%</div><div class="stat-label">CPU（{{ host.cpu_count }} 核）</div></div></el-col>
            <el-col :span="6"><div class="page-card stat"><div class="stat-num">{{ host.mem_percent }}%</div><div class="stat-label">内存 {{ host.mem_used_gb }}/{{ host.mem_total_gb }} GB</div></div></el-col>
            <el-col :span="6"><div class="page-card stat"><div class="stat-num">{{ host.disk_percent }}%</div><div class="stat-label">磁盘 {{ host.disk_used_gb }}/{{ host.disk_total_gb }} GB</div></div></el-col>
            <el-col :span="6"><div class="page-card stat"><div class="stat-num">{{ host.proc_mem_mb }}</div><div class="stat-label">平台进程内存 MB</div></div></el-col>
          </el-row>
          <div v-if="host" class="muted tiny" style="margin-top: 8px">主机启动时间: {{ host.boot_time }}</div>
          <el-empty v-else description="加载中" :image-size="60" />
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- ============ 新建/编辑对话框 ============ -->
    <el-dialog v-model="dlg.visible" :title="dlg.isEdit ? '编辑监控规则' : '新建监控规则'" width="680px" destroy-on-close top="5vh">
      <el-form :model="form" label-width="100px">
        <el-form-item label="规则名称" required>
          <el-input v-model="form.name" placeholder="例如：ods_orders 行数波动监控" maxlength="60" />
        </el-form-item>
        <el-form-item label="规则类型" required>
          <el-select v-model="form.rule_type" style="width: 100%" @change="onTypeChange">
            <el-option v-for="t in typeOptions" :key="t.v" :label="t.l" :value="t.v" />
          </el-select>
        </el-form-item>

        <template v-if="form.rule_type !== 'task_duration'">
          <el-form-item label="数据源" required>
            <el-select v-model="form.datasource_id" filterable style="width: 100%" @change="onDsChange">
              <el-option v-for="d in datasources" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
            </el-select>
          </el-form-item>
          <el-form-item label="监控表" required>
            <el-select v-model="form.table_name" filterable style="width: 100%" @change="onTableChange">
              <el-option v-for="t in metaTables" :key="t.id" :label="t.name" :value="t.name" />
            </el-select>
          </el-form-item>
          <el-form-item v-if="needColumn" label="监控列" required>
            <el-select v-model="form.column_name" filterable style="width: 100%">
              <el-option v-for="c in metaColumns" :key="c.name" :label="`${c.name} (${c.type})`" :value="c.name" />
            </el-select>
          </el-form-item>
        </template>
        <el-form-item v-else label="工作流" required>
          <el-select v-model="form.params.workflow_name" filterable style="width: 100%">
            <el-option v-for="w in workflows" :key="w.id" :label="w.name" :value="w.name" />
          </el-select>
        </el-form-item>

        <!-- 类型参数 -->
        <el-form-item v-if="form.rule_type === 'row_count_fluctuation'" label="波动阈值%">
          <el-input-number v-model="form.params.pct" :min="1" :max="1000" />
          <span class="muted tiny" style="margin-left: 8px">相对基线波动超过该百分比告警（基线自动建立）</span>
        </el-form-item>
        <el-form-item v-if="form.rule_type === 'row_count_threshold'" label="行数下限">
          <el-input-number v-model="form.params.min" :min="0" />
          <span style="margin: 0 8px" class="muted">上限</span>
          <el-input-number v-model="form.params.max" :min="0" />
          <span class="muted tiny" style="margin-left: 8px">留 0 表示不限</span>
        </el-form-item>
        <el-form-item v-if="form.rule_type === 'null_rate'" label="空值率阈值%">
          <el-input-number v-model="form.params.pct" :min="0" :max="100" />
        </el-form-item>
        <el-form-item v-if="form.rule_type === 'freshness'" label="延迟阈值(时)">
          <el-input-number v-model="form.params.hours" :min="1" :max="8760" />
          <span class="muted tiny" style="margin-left: 8px">监控列最大时间距今超过该小时数告警</span>
        </el-form-item>
        <el-form-item v-if="form.rule_type === 'task_duration'" label="时长阈值(分)">
          <el-input-number v-model="form.params.max_minutes" :min="0.5" :step="0.5" />
        </el-form-item>
        <el-form-item v-if="form.rule_type === 'sql_check'" label="校验 SQL" required>
          <el-input v-model="form.params.sql" type="textarea" :rows="4" class="mono"
                    placeholder="只读 SELECT；返回的每一行都视为问题数据，行数 > 0 即告警" />
        </el-form-item>

        <el-row>
          <el-col :span="12">
            <el-form-item label="规则分组">
              <el-input v-model="form.group_name" placeholder="例如：数仓质量 / 核心表监控" maxlength="30" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="告警静默">
              <el-input-number v-model="form.mute_hours" :min="0" :max="720" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <div class="muted tiny" style="margin: -8px 0 10px 100px">
          静默窗口：规则告警后 N 小时内不重复通知（0 = 每次失败都通知）；检查记录照常落库
        </div>

        <el-row>
          <el-col :span="12">
            <el-form-item label="检查调度" required>
              <el-input v-model="form.cron" placeholder="0 6 * * *（每天 6 点）" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="严重度">
              <el-select v-model="form.severity" style="width: 100%">
                <el-option label="警告 warning" value="warning" />
                <el-option label="严重 critical" value="critical" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item v-if="!dlg.isEdit" label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="dlg.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, reactive, ref, computed, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import http, { errMsg } from '../api'
import { openAi } from '../ai'

const route = useRoute()
const tab = ref('rules')
const loading = ref(false)
const rules = ref([])
const datasources = ref([])
const workflows = ref([])
const sum = ref({})
const groupFilter = ref('')

const TYPE_MAP = {
  row_count_fluctuation: '行数波动',
  row_count_threshold: '行数阈值',
  null_rate: '空值率',
  unique_check: '唯一性',
  freshness: '数据延迟',
  sql_check: '自定义SQL',
  task_duration: '任务时长',
}
const typeOptions = Object.entries(TYPE_MAP).map(([v, l]) => ({ v, l }))
const typeLabel = (t) => TYPE_MAP[t] || t
const statusTag = (s) => ({ passed: 'success', failed: 'danger', error: 'warning' }[s] || 'info')
const statusLabel = (s) => ({ passed: '通过', failed: '失败', error: '异常' }[s] || s)

const dlg = reactive({ visible: false, isEdit: false, id: null, saving: false })
const form = reactive({
  name: '', rule_type: 'row_count_fluctuation', datasource_id: null, table_name: '',
  column_name: '', params: { pct: 30 }, cron: '0 6 * * *', severity: 'warning', enabled: true,
  group_name: '', mute_hours: 24,
})
const metaTables = ref([])
const metaColumns = ref([])

const groups = computed(() => [...new Set(rules.value.map((r) => r.group_name).filter(Boolean))])
const filteredRules = computed(() =>
  groupFilter.value ? rules.value.filter((r) => r.group_name === groupFilter.value) : rules.value)

// ---- 趋势图 ----
const trendEl = ref(null)
let trendChart = null

async function loadTrend() {
  if (!trendEl.value) return
  try {
    const d = await http.get('/api/monitor/trend', {
      params: { days: 7, rule_id: checkQ.rule_id || undefined },
    })
    if (!trendChart) trendChart = echarts.init(trendEl.value)
    trendChart.setOption({
      grid: { left: 40, right: 16, top: 30, bottom: 26 },
      tooltip: { trigger: 'axis' },
      legend: { top: 0 },
      xAxis: { type: 'category', data: d.days.map((b) => b.day) },
      yAxis: { type: 'value', minInterval: 1 },
      series: [
        { name: '通过', type: 'bar', stack: 's', data: d.days.map((b) => b.passed),
          itemStyle: { color: '#67c23a' }, barMaxWidth: 30 },
        { name: '失败', type: 'bar', stack: 's', data: d.days.map((b) => b.failed),
          itemStyle: { color: '#f56c6c' } },
        { name: '异常', type: 'bar', stack: 's', data: d.days.map((b) => b.error),
          itemStyle: { color: '#e6a23c' } },
      ],
    }, true)
    trendChart.resize()
  } catch { /* 趋势加载失败不阻塞记录表 */ }
}

function onResize() { trendChart && trendChart.resize() }

const checkQ = reactive({ rule_id: Number(route.query.rule_id) || null, status: '' })
const host = ref(null)
const hostAuto = ref(false)
let hostTimer = null

async function loadHost() {
  try { host.value = await http.get('/api/system/metrics') } catch { /* ignore */ }
}

function watchHost() {
  clearInterval(hostTimer)
  if (hostAuto.value) hostTimer = setInterval(loadHost, 10000)
}
watch(hostAuto, watchHost)
const checks = ref([])
const checkTotal = ref(0)
const checkPage = ref(1)
const checkSize = 30
const checkLoading = ref(false)

function blankParams(t) {
  return {
    row_count_fluctuation: { pct: 30 },
    row_count_threshold: { min: 1, max: 0 },
    null_rate: { pct: 5 },
    unique_check: {},
    freshness: { hours: 24 },
    sql_check: { sql: '' },
    task_duration: { workflow_name: '', max_minutes: 30 },
  }[t] || {}
}

async function loadSummary() {
  sum.value = await http.get('/api/monitor/summary')
}

async function loadRules() {
  loading.value = true
  try {
    rules.value = await http.get('/api/monitor/rules')
    loadSummary()
  } finally {
    loading.value = false
  }
}

async function loadChecks(page) {
  if (page) checkPage.value = page
  checkLoading.value = true
  try {
    const d = await http.get('/api/monitor/checks', {
      params: { rule_id: checkQ.rule_id || undefined, status: checkQ.status || undefined,
                page: checkPage.value, size: checkSize },
    })
    checks.value = d.items
    checkTotal.value = d.total
  } finally {
    checkLoading.value = false
  }
  loadTrend()
}

function onTab() {
  if (tab.value === 'checks') {
    loadChecks()
    setTimeout(() => { trendChart && trendChart.resize() }, 100)
  } else loadRules()
  if (tab.value === 'host') { loadHost(); watchHost() }
  else clearInterval(hostTimer)
}

async function openDlg(row) {
  dlg.isEdit = !!row
  dlg.id = row?.id || null
  if (row) {
    Object.assign(form, JSON.parse(JSON.stringify(row)))
    form.params = row.params || blankParams(row.rule_type)
    if (row.datasource_id) { await onDsChange(); }
    if (row.table_name) { await onTableChange(); }
  } else {
    Object.assign(form, {
      name: '', rule_type: 'row_count_fluctuation', datasource_id: null, table_name: '',
      column_name: '', params: { pct: 30 }, cron: '0 6 * * *', severity: 'warning', enabled: true,
      group_name: '', mute_hours: 24,
    })
    metaTables.value = []
    metaColumns.value = []
  }
  dlg.visible = true
}

function onTypeChange() {
  form.params = blankParams(form.rule_type)
  form.column_name = ''
}

const needColumn = computed(() => ['null_rate', 'unique_check', 'freshness'].includes(form.rule_type))

async function onDsChange() {
  metaTables.value = []
  metaColumns.value = []
  form.table_name = ''
  if (!form.datasource_id) return
  const d = await http.get('/api/meta/tables', { params: { datasource_id: form.datasource_id, size: 200 } })
  metaTables.value = d.items || []
}

async function onTableChange() {
  metaColumns.value = []
  form.column_name = ''
  if (!form.table_name) return
  const t = metaTables.value.find((x) => x.name === form.table_name)
  if (!t) return
  const d = await http.get(`/api/meta/tables/${t.id}`)
  metaColumns.value = d.columns || []
}

async function save() {
  if (!form.name.trim()) { ElMessage.warning('请填写规则名称'); return }
  dlg.saving = true
  try {
    const payload = {
      name: form.name, rule_type: form.rule_type,
      datasource_id: form.rule_type === 'task_duration' ? 0 : (form.datasource_id || 0),
      table_name: form.rule_type === 'task_duration' ? '' : form.table_name,
      column_name: form.rule_type === 'task_duration' ? '' : form.column_name,
      params: form.params, cron: form.cron, severity: form.severity, enabled: form.enabled,
      group_name: form.group_name || '', mute_hours: form.mute_hours ?? 24,
    }
    if (dlg.isEdit) await http.put(`/api/monitor/rules/${dlg.id}`, payload)
    else await http.post('/api/monitor/rules', payload)
    dlg.visible = false
    ElMessage.success(dlg.isEdit ? '已保存' : '规则已创建')
    loadRules()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    dlg.saving = false
  }
}

async function runNow(row) {
  try {
    const r = await http.post(`/api/monitor/rules/${row.id}/run`)
    if (r.status === 'failed') ElMessage.error(`检查失败：${r.message || r.metric}`)
    else if (r.status === 'error') ElMessage.error(r.message)
    else ElMessage.success(`检查通过：${r.metric}`)
    loadRules()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function toggle(row) {
  try {
    await http.put(`/api/monitor/rules/${row.id}`, {
      name: row.name, rule_type: row.rule_type, datasource_id: row.datasource_id,
      table_name: row.table_name, column_name: row.column_name, params: row.params,
      cron: row.cron, severity: row.severity, enabled: !row.enabled,
      group_name: row.group_name || '', mute_hours: row.mute_hours ?? 24,
    })
    ElMessage.success(row.enabled ? '已停用' : '已启用')
    loadRules()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function removeRule(row) {
  try {
    await ElMessageBox.confirm(`删除监控规则「${row.name}」？检查记录将一并删除。`, '删除确认',
                               { type: 'warning', confirmButtonText: '删除' })
  } catch { return }
  try {
    await http.delete(`/api/monitor/rules/${row.id}`)
    ElMessage.success('已删除')
    loadRules()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onBeforeUnmount(() => clearInterval(hostTimer))
onMounted(async () => {
  window.addEventListener('resize', onResize)
  datasources.value = await http.get('/api/datasources')
  workflows.value = await http.get('/api/sched/workflows')
  await loadRules()
  if (route.query.rule_id || tab.value === 'checks') {
    tab.value = 'checks'
    loadChecks()
  }
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (trendChart) { trendChart.dispose(); trendChart = null }
})
</script>

<style scoped>
.stat {
  text-align: center;
  padding: 14px 0;
}
.stat-num {
  font-size: 26px;
  font-weight: 700;
  color: #1d2939;
}
.stat-num.stat-bad {
  color: #f56c6c;
}
.stat-label {
  font-size: 12px;
  color: #98a2b3;
  margin-top: 2px;
}
.tiny {
  font-size: 11px;
}
.mono {
  font-family: 'JetBrains Mono', Menlo, Consolas, monospace;
}
</style>
