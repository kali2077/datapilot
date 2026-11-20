<template>
  <div>
    <div class="page-card">
      <el-tabs v-model="tab" @tab-change="onTab">
        <!-- ============ 实例管理 ============ -->
        <el-tab-pane label="运行实例" name="instances">
          <div class="toolbar">
            <el-select v-model="instQ.workflow_id" placeholder="全部工作流" clearable filterable style="width: 220px" @change="loadInstances(1)">
              <el-option v-for="w in workflows" :key="w.id" :label="w.name" :value="w.id" />
            </el-select>
            <el-select v-model="instQ.status" placeholder="全部状态" clearable style="width: 130px" @change="loadInstances(1)">
              <el-option v-for="s in instanceStatuses" :key="s.v" :label="s.l" :value="s.v" />
            </el-select>
            <el-button icon="Search" @click="loadInstances(1)">筛选</el-button>
            <div class="spacer" />
            <span class="muted">共 {{ instTotal }} 条</span>
            <el-button icon="Refresh" circle @click="loadInstances()" />
          </div>

          <el-table v-loading="instLoading" :data="instances" stripe highlight-current-row @row-click="openInstance">
            <el-table-column prop="id" label="#" width="64" />
            <el-table-column prop="workflow_name" label="工作流" min-width="170" />
            <el-table-column label="触发方式" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="triggerTag(row.trigger_type)">{{ triggerLabel(row.trigger_type) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="logical_date" label="逻辑日期" width="170" />
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <span class="status-cell" :class="{ failed: row.status === 'failed' }"><span class="dp-dot" :class="({ success: 'ok', failed: 'danger', running: 'run' }[row.status] || 'idle')" />{{ statusLabel(row.status) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="started_at" label="开始时间" width="165">
              <template #default="{ row }">{{ row.started_at || '-' }}</template>
            </el-table-column>
            <el-table-column prop="finished_at" label="结束时间" width="165">
              <template #default="{ row }">{{ row.finished_at || '-' }}</template>
            </el-table-column>
            <el-table-column label="耗时" width="90" align="right">
              <template #default="{ row }">{{ timeDiff(row.started_at, row.finished_at) }}</template>
            </el-table-column>
            <el-table-column prop="triggered_by" label="触发人" width="100">
              <template #default="{ row }">{{ row.triggered_by || '-' }}</template>
            </el-table-column>
            <template #empty><el-empty description="暂无实例" /></template>
          </el-table>
          <div style="display: flex; justify-content: flex-end; margin-top: 12px">
            <el-pagination
              v-model:current-page="instPage" :page-size="instSize" :total="instTotal"
              layout="total, prev, pager, next" background @current-change="loadInstances()"
            />
          </div>
        </el-tab-pane>

        <!-- ============ 运行矩阵（Grid：实例 × 节点） ============ -->
        <el-tab-pane label="运行矩阵" name="matrix">
          <div class="toolbar">
            <el-select v-model="mx.wfId" placeholder="选择工作流" filterable style="width: 220px" @change="loadMatrix">
              <el-option v-for="w in workflows" :key="w.id" :label="w.name" :value="w.id" />
            </el-select>
            <el-select v-model="mx.size" style="width: 110px" @change="loadMatrix">
              <el-option :value="10" label="最近 10" />
              <el-option :value="15" label="最近 15" />
              <el-option :value="30" label="最近 30" />
              <el-option :value="50" label="最近 50" />
            </el-select>
            <el-checkbox v-model="mx.auto" @change="onMxAuto">自动刷新(30s)</el-checkbox>
            <div class="spacer" />
            <el-button icon="Refresh" circle @click="loadMatrix" />
          </div>

          <template v-if="mx.data">
            <div class="muted" style="margin-bottom: 8px">
              {{ mx.data.workflow.name }} · Cron {{ mx.data.workflow.schedule_cron || '手动' }} ·
              {{ mx.data.rows.length }} 个实例（新→旧），点击色块查看实例日志
            </div>
            <el-table v-loading="mx.loading" :data="mx.data.rows" size="small" border>
              <el-table-column label="实例" width="215" fixed="left">
                <template #default="{ row }">
                  <span class="mx-inst" @click="openInstanceById(row.instance_id)">
                    <span class="dp-dot" :class="dotClass(row.status)" />
                    {{ (row.logical_date || '').slice(5, 16) }}
                    <el-tag size="small" :type="triggerTag(row.trigger_type)" effect="plain">{{ triggerLabel(row.trigger_type) }}</el-tag>
                  </span>
                </template>
              </el-table-column>
              <el-table-column v-for="n in mx.data.nodes" :key="n.id" min-width="100" align="center">
                <template #header>
                  <span>{{ n.name }} <el-tag size="small" effect="plain">{{ n.node_type }}</el-tag></span>
                </template>
                <template #default="{ row }">
                  <span
                    v-if="row.cells[n.id]" class="mx-cell" :class="`mx-${row.cells[n.id].status}`"
                    :title="`${n.name} · ${statusLabel(row.cells[n.id].status)}${row.cells[n.id].duration_ms ? ' · ' + fmtDuration(row.cells[n.id].duration_ms) : ''}`"
                    @click="openInstanceById(row.instance_id)"
                  />
                  <span v-else class="mx-cell mx-none" title="未运行" />
                </template>
              </el-table-column>
              <template #empty><el-empty description="暂无实例" /></template>
            </el-table>
            <div class="muted" style="margin-top: 8px">
              图例：<span class="mx-cell mx-success" />成功 <span class="mx-cell mx-failed" />失败
              <span class="mx-cell mx-running" />运行中 <span class="mx-cell mx-waiting" />等待
              <span class="mx-cell mx-skipped" />跳过 <span class="mx-cell mx-none" />未运行
            </div>
          </template>
          <el-empty v-else-if="!mx.wfId" description="选择工作流查看运行矩阵" />
        </el-tab-pane>

        <!-- ============ 调度引擎（Airflow 侧视角） ============ -->
        <el-tab-pane label="调度引擎" name="engine">
          <template v-if="eng.status">
            <el-alert
              v-if="eng.status.engine !== 'airflow'" type="info" :closable="false" show-icon
              title="当前为内置调度引擎（local）" description="引擎面板面向 Airflow 模式；local 模式下若下方显示不可达属正常。"
              style="margin-bottom: 12px"
            />
            <div class="eng-head">
              <span class="dp-dot" :class="eng.status.reachable ? 'ok' : 'danger'" />
              <b>{{ eng.status.engine === 'airflow' ? 'Airflow' : 'Local (APScheduler)' }}</b>
              <el-tag v-if="eng.status.version" size="small" effect="plain">v{{ eng.status.version }}</el-tag>
              <span class="muted">{{ eng.status.reachable ? '引擎可达' : `不可达：${eng.status.error}` }}</span>
              <div class="spacer" />
              <el-button icon="Refresh" circle @click="loadEngine" />
            </div>

            <el-table v-loading="eng.loading" :data="eng.dags" stripe>
              <el-table-column prop="dag_id" label="DAG" min-width="190" class-name="mono" />
              <el-table-column label="平台工作流" width="110">
                <template #default="{ row }">
                  <el-link v-if="row.workflow_id" type="primary" class="mono"
                           @click="$router.push(`/ops?workflow_id=${row.workflow_id}`)">#{{ row.workflow_id }}</el-link>
                  <span v-else class="muted">-</span>
                </template>
              </el-table-column>
              <el-table-column label="解析" width="90">
                <template #default="{ row }">
                  <el-tag v-if="row.has_import_errors" type="danger" size="small">异常</el-tag>
                  <el-tag v-else type="success" size="small">正常</el-tag>
                </template>
              </el-table-column>
              <el-table-column label="暂停" width="80">
                <template #default="{ row }">
                  <el-switch :model-value="row.is_paused" :disabled="!row.is_active" @change="(v) => toggleDagPaused(row, v)" />
                </template>
              </el-table-column>
              <el-table-column label="最近解析" width="165">
                <template #default="{ row }">{{ fmtAfTime(row.last_parsed_time) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="160" fixed="right">
                <template #default="{ row }">
                  <el-button link type="primary" @click="openEngRuns(row)">运行历史</el-button>
                  <el-button link type="success" @click="triggerDag(row)">触发</el-button>
                </template>
              </el-table-column>
              <template #empty><el-empty description="暂无 DataPilot DAG（Airflow 模式下创建工作流后生成）" /></template>
            </el-table>
          </template>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- 实例详情抽屉 -->
    <el-drawer v-model="instDlg.visible" :title="`实例 #${instDlg.info?.id ?? ''}：${instDlg.info?.workflow_name || ''}`" size="720px">
      <template v-if="instDlg.info">
        <el-descriptions :column="3" size="small" border style="margin-bottom: 14px">
          <el-descriptions-item label="状态">
            <span class="status-cell" :class="{ failed: instDlg.info.status === 'failed' }"><span class="dp-dot" :class="({ success: 'ok', failed: 'danger', running: 'run' }[instDlg.info.status] || 'idle')" />{{ statusLabel(instDlg.info.status) }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="触发方式">{{ triggerLabel(instDlg.info.trigger_type) }}</el-descriptions-item>
          <el-descriptions-item label="触发人">{{ instDlg.info.triggered_by || '-' }}</el-descriptions-item>
          <el-descriptions-item label="逻辑日期" :span="3">{{ instDlg.info.logical_date || '-' }}</el-descriptions-item>
          <el-descriptions-item label="开始">{{ instDlg.info.started_at || '-' }}</el-descriptions-item>
          <el-descriptions-item label="结束">{{ instDlg.info.finished_at || '-' }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ timeDiff(instDlg.info.started_at, instDlg.info.finished_at) }}</el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="instDlg.info.error" type="error" :closable="false" show-icon style="margin-bottom: 12px"
          title="实例错误" :description="instDlg.info.error"
        />

        <div style="display: flex; align-items: center; gap: 10px; margin-bottom: 10px">
          <span style="font-weight: 600">节点运行（{{ instDlg.info.nodes?.length || 0 }}）</span>
          <div style="flex: 1" />
          <el-checkbox v-model="instDlg.onlyFailed" size="small">仅重跑失败节点</el-checkbox>
          <el-button
            size="small" icon="ChatDotRound"
            @click="openAi(`诊断一下实例 ${instDlg.info.id}，分析失败原因并给出处理建议`)"
          >AI 诊断</el-button>
          <el-button
            type="warning" size="small" icon="RefreshRight" :loading="instDlg.rerunning"
            @click="rerunInstance(instDlg.info)"
          >实例重跑</el-button>
        </div>

        <el-table :data="instDlg.info.nodes || []" size="small" border>
          <el-table-column type="expand">
            <template #default="{ row }">
              <div style="padding: 10px 14px">
                <div class="muted" style="margin-bottom: 6px">运行日志（{{ row.log ? row.log.length : 0 }} 字符）</div>
                <pre class="log-pre">{{ row.log || '(暂无日志)' }}</pre>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="name" label="节点" min-width="140" />
          <el-table-column label="类型" width="80">
            <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.node_type }}</el-tag></template>
          </el-table-column>
          <el-table-column label="状态" width="95">
            <template #default="{ row }">
              <span class="status-cell" :class="{ failed: row.status === 'failed' }"><span class="dp-dot" :class="({ success: 'ok', failed: 'danger', running: 'run' }[row.status] || 'idle')" />{{ statusLabel(row.status) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="90" align="right">
            <template #default="{ row }">{{ fmtDuration(row.duration_ms) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="90" fixed="right">
            <template #default="{ row }">
              <el-button link type="warning" :loading="row._rerunning" @click="rerunNode(row)">重跑</el-button>
            </template>
          </el-table-column>
          <template #empty><el-empty description="暂无节点运行记录" :image-size="60" /></template>
        </el-table>
      </template>
      <div v-else v-loading="true" style="height: 300px" />
    </el-drawer>

    <!-- Airflow 运行历史抽屉 -->
    <el-drawer v-model="engRuns.visible" :title="`Airflow 运行历史：${engRuns.dagId}`" size="820px">
      <el-table v-loading="engRuns.loading" :data="engRuns.rows" size="small" border @expand-change="loadEngTasks">
        <el-table-column type="expand">
          <template #default="{ row }">
            <div style="padding: 8px 12px">
              <template v-if="engRuns.tasks[row.dag_run_id]">
                <el-table :data="engRuns.tasks[row.dag_run_id]" size="small" border>
                  <el-table-column prop="task_id" label="任务（=平台节点）" min-width="160" class-name="mono" />
                  <el-table-column label="状态" width="95">
                    <template #default="{ row: t }">
                      <span class="status-cell"><span class="dp-dot" :class="dotClass(afToStatus(t.state))" />{{ afStateLabel(t.state) }}</span>
                    </template>
                  </el-table-column>
                  <el-table-column prop="try_number" label="尝试" width="60" align="center" />
                  <el-table-column label="开始" width="160">
                    <template #default="{ row: t }">{{ fmtAfTime(t.start_date) || '-' }}</template>
                  </el-table-column>
                  <el-table-column label="结束" width="160">
                    <template #default="{ row: t }">{{ fmtAfTime(t.end_date) || '-' }}</template>
                  </el-table-column>
                </el-table>
              </template>
              <div v-else class="muted" style="padding: 6px 0">加载任务实例中…</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column prop="dag_run_id" label="Run ID" min-width="230" class-name="mono" show-overflow-tooltip />
        <el-table-column label="状态" width="95">
          <template #default="{ row }">
            <span class="status-cell"><span class="dp-dot" :class="dotClass(afToStatus(row.state))" />{{ afStateLabel(row.state) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="run_type" label="类型" width="90">
          <template #default="{ row }">{{ row.run_type || '-' }}</template>
        </el-table-column>
        <el-table-column label="逻辑日期" width="160">
          <template #default="{ row }">{{ fmtAfTime(row.logical_date) || '-' }}</template>
        </el-table-column>
        <el-table-column label="开始" width="160">
          <template #default="{ row }">{{ fmtAfTime(row.start_date) || '-' }}</template>
        </el-table-column>
        <template #empty><el-empty description="暂无运行记录" /></template>
      </el-table>
    </el-drawer>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import http, { errMsg, fmtDuration, timeDiff } from '../api'
import { openAi } from '../ai'

const route = useRoute()
const tab = ref('instances')

const workflows = ref([])
const instances = ref([])
const instTotal = ref(0)
const instPage = ref(1)
const instSize = 20
const instLoading = ref(false)
const instQ = reactive({ workflow_id: null, status: '' })

const instanceStatuses = [
  { v: 'success', l: '成功' },
  { v: 'failed', l: '失败' },
  { v: 'running', l: '运行中' },
  { v: 'waiting', l: '等待中' },
  { v: 'skipped', l: '跳过' },
]

const instDlg = reactive({ visible: false, info: null, onlyFailed: false, rerunning: false })

function statusLabel(s) {
  return { success: '成功', failed: '失败', running: '运行中', waiting: '等待', skipped: '跳过' }[s] || (s || '-')
}
function triggerTag(t) {
  return { manual: 'primary', scheduled: 'warning', rerun: 'danger' }[t] || 'info'
}
function triggerLabel(t) {
  return { manual: '手动', scheduled: '调度', rerun: '重跑' }[t] || (t || '-')
}

async function loadWorkflows() {
  try {
    workflows.value = await http.get('/api/sched/workflows')
  } catch { /* ignore */ }
}

async function loadInstances(p) {
  if (p) instPage.value = p
  instLoading.value = true
  try {
    const res = await http.get('/api/sched/instances', {
      params: { workflow_id: instQ.workflow_id || 0, status: instQ.status || '', page: instPage.value, size: instSize },
    })
    instances.value = res.items
    instTotal.value = res.total
  } catch { /* ignore */ } finally {
    instLoading.value = false
  }
}

async function openInstance(row) {
  await openInstanceById(row.id)
}

async function openInstanceById(id) {
  instDlg.info = null
  instDlg.onlyFailed = false
  instDlg.visible = true
  try {
    instDlg.info = await http.get(`/api/sched/instances/${id}`)
  } catch { /* ignore */ }
}

async function refreshInstance() {
  if (instDlg.info) {
    try {
      instDlg.info = await http.get(`/api/sched/instances/${instDlg.info.id}`)
    } catch { /* ignore */ }
  }
  loadInstances()
}

async function rerunInstance(inst) {
  instDlg.rerunning = true
  try {
    await http.post(`/api/sched/instances/${inst.id}/rerun?only_failed=${instDlg.onlyFailed ? 'true' : 'false'}`)
    ElMessage.success('重跑已提交')
    refreshInstance()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    instDlg.rerunning = false
  }
}

async function rerunNode(run) {
  run._rerunning = true
  try {
    await http.post(`/api/sched/nodes/${run.id}/rerun`)
    ElMessage.success(`节点「${run.name}」重跑已提交`)
    setTimeout(refreshInstance, 600)
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    run._rerunning = false
  }
}

/* ---------- 运行矩阵（Grid） ---------- */
const mx = reactive({ wfId: null, size: 15, auto: false, data: null, loading: false })
let mxTimer = null

function dotClass(s) {
  return { success: 'ok', failed: 'danger', running: 'run', skipped: 'warn', waiting: 'idle' }[s] || 'idle'
}

async function loadMatrix() {
  if (!mx.wfId) return
  mx.loading = true
  try {
    mx.data = await http.get('/api/sched/matrix', { params: { workflow_id: mx.wfId, size: mx.size } })
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    mx.loading = false
  }
}

function onMxAuto(v) {
  clearInterval(mxTimer)
  if (v) mxTimer = setInterval(loadMatrix, 30000)
}

/* ---------- 调度引擎（Airflow 侧） ---------- */
const eng = reactive({ status: null, dags: [], loading: false })
const engRuns = reactive({ visible: false, dagId: '', rows: [], loading: false, tasks: {} })

async function loadEngine() {
  eng.loading = true
  try {
    eng.status = await http.get('/api/airflow/status')
    eng.dags = []
    if (eng.status.reachable) {
      const res = await http.get('/api/airflow/dags')
      eng.dags = res.dags || []
    }
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    eng.loading = false
  }
}

async function toggleDagPaused(row, v) {
  try {
    await http.patch(`/api/airflow/dags/${row.dag_id}`, { is_paused: v })
    row.is_paused = v
    ElMessage.success(v ? 'DAG 已暂停' : 'DAG 已恢复')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function triggerDag(row) {
  try {
    await http.post(`/api/airflow/dags/${row.dag_id}/trigger`)
    ElMessage.success('已触发，稍后刷新运行历史查看')
    if (engRuns.visible && engRuns.dagId === row.dag_id) loadEngRuns()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function openEngRuns(row) {
  engRuns.dagId = row.dag_id
  engRuns.visible = true
  await loadEngRuns()
}

async function loadEngRuns() {
  engRuns.loading = true
  try {
    const res = await http.get(`/api/airflow/dags/${engRuns.dagId}/runs`, { params: { limit: 25 } })
    engRuns.rows = res.runs || []
    engRuns.tasks = {}
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    engRuns.loading = false
  }
}

async function loadEngTasks(row) {
  if (!row?.dag_run_id || engRuns.tasks[row.dag_run_id]) return
  try {
    const res = await http.get(`/api/airflow/dags/${engRuns.dagId}/runs/${encodeURIComponent(row.dag_run_id)}/tasks`)
    engRuns.tasks = { ...engRuns.tasks, [row.dag_run_id]: res.tasks || [] }
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

function afToStatus(s) {
  // Airflow 状态 -> 平台色点语义
  return { success: 'success', failed: 'failed', running: 'running', queued: 'waiting' }[s] || 'waiting'
}
function afStateLabel(s) {
  return { success: '成功', failed: '失败', running: '运行中', queued: '排队' }[s] || (s || '未开始')
}
function fmtAfTime(t) {
  return (t || '').replace('T', ' ').slice(0, 19)
}

function onTab(name) {
  if (name === 'matrix' && !mx.data) {
    if (!mx.wfId && workflows.value.length) mx.wfId = workflows.value[0].id
    loadMatrix()
  }
  if (name === 'engine' && !eng.status) loadEngine()
}

onMounted(() => {
  // 支持 ?workflow_id= 快速过滤（监控页告警记录深链 ?instance_id= 直接打开实例抽屉）
  const wf = Number(route.query.workflow_id || 0)
  if (wf) {
    instQ.workflow_id = wf
    mx.wfId = wf
  }
  loadWorkflows()
  loadInstances(1)
  const inst = Number(route.query.instance_id || 0)
  if (inst) openInstanceById(inst)
})

onUnmounted(() => clearInterval(mxTimer))
</script>

<style scoped>
.status-cell {
  display: inline-flex;
  align-items: center;
  font-size: 12.5px;
  color: var(--dp-text-2);
}
.status-cell.failed {
  color: var(--dp-danger);
}

/* 运行矩阵色块 */
.mx-cell {
  display: inline-block;
  width: 16px;
  height: 16px;
  border-radius: 4px;
  cursor: pointer;
  margin-right: 4px;
  vertical-align: middle;
}
.mx-success { background: var(--dp-ok, #67c23a); }
.mx-failed { background: var(--dp-danger, #f56c6c); }
.mx-running { background: var(--dp-accent, #409eff); animation: mx-blink 1.2s infinite alternate; }
.mx-waiting { background: var(--dp-idle, #c0c4cc); }
.mx-skipped { background: var(--dp-warn, #e6a23c); }
.mx-none { background: transparent; border: 1px dashed var(--dp-idle, #c0c4cc); cursor: default; }
@keyframes mx-blink {
  from { opacity: 0.45; }
  to { opacity: 1; }
}
.mx-inst {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.eng-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
</style>
