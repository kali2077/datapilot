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

        <!-- ============ 告警通道 ============ -->
        <el-tab-pane label="告警通道" name="channels">
          <div class="toolbar">
            <span class="muted">任务失败/超时时通知的渠道（管理员维护）</span>
            <div class="spacer" />
            <el-button v-if="auth.isAdmin" type="primary" icon="Plus" @click="openChannel()">新建通道</el-button>
          </div>
          <el-table v-loading="chLoading" :data="channels" stripe>
            <el-table-column prop="name" label="名称" min-width="160" />
            <el-table-column label="类型" width="100">
              <template #default="{ row }">
                <el-tag size="small" :type="row.type === 'email' ? 'primary' : 'warning'">{{ row.type }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="配置" min-width="300" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono muted">{{ channelSummary(row) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '是' : '否' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column v-if="auth.isAdmin" label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openChannel(row)">编辑</el-button>
                <el-button link type="danger" @click="removeChannel(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无告警通道" /></template>
          </el-table>
        </el-tab-pane>

        <!-- ============ 告警记录 ============ -->
        <el-tab-pane label="告警记录" name="records">
          <div class="toolbar">
            <span class="muted">共 {{ recTotal }} 条</span>
            <div class="spacer" />
            <el-button icon="Refresh" circle @click="loadRecords" />
          </div>
          <el-table v-loading="recLoading" :data="records" stripe>
            <el-table-column prop="id" label="#" width="64" />
            <el-table-column prop="channel" label="通道" width="150" />
            <el-table-column prop="title" label="告警内容" min-width="320" show-overflow-tooltip />
            <el-table-column label="实例" width="90">
              <template #default="{ row }">
                <el-link v-if="row.instance_id" type="primary" @click.stop="openInstanceById(row.instance_id)">#{{ row.instance_id }}</el-link>
                <span v-else>-</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'sent' ? 'success' : 'danger'">{{ row.status || '-' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="ts" label="时间" width="170" />
            <template #empty><el-empty description="暂无告警记录" /></template>
          </el-table>
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

    <!-- 告警通道对话框 -->
    <el-dialog v-model="chDlg.visible" :title="chDlg.isEdit ? '编辑告警通道' : '新建告警通道'" width="520px" destroy-on-close>
      <el-form :model="chDlg.form" label-width="90px">
        <el-form-item label="名称" required><el-input v-model="chDlg.form.name" /></el-form-item>
        <el-form-item label="类型" required>
          <el-radio-group v-model="chDlg.form.type">
            <el-radio-button value="email">Email</el-radio-button>
            <el-radio-button value="webhook">Webhook</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <template v-if="chDlg.form.type === 'email'">
          <el-form-item label="收件人" required>
            <el-input v-model="chDlg.form.config.to" placeholder="a@x.com, b@x.com" />
          </el-form-item>
          <el-form-item label="SMTP 主机" required><el-input v-model="chDlg.form.config.smtp_host" /></el-form-item>
          <el-form-item label="SMTP 端口"><el-input-number v-model="chDlg.form.config.smtp_port" :min="1" :max="65535" /></el-form-item>
          <el-form-item label="用户名"><el-input v-model="chDlg.form.config.username" /></el-form-item>
          <el-form-item label="密码/授权码"><el-input v-model="chDlg.form.config.password" type="password" show-password /></el-form-item>
        </template>
        <template v-else>
          <el-form-item label="URL" required><el-input v-model="chDlg.form.config.url" placeholder="https://..." /></el-form-item>
          <el-form-item label="Secret"><el-input v-model="chDlg.form.config.secret" placeholder="可选，用于签名" /></el-form-item>
        </template>
        <el-form-item label="启用"><el-switch v-model="chDlg.form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="chDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="chDlg.saving" @click="saveChannel">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg, fmtDuration, timeDiff } from '../api'
import { openAi } from '../ai'
import { useAuthStore } from '../store'

const route = useRoute()
const auth = useAuthStore()
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

const channels = ref([])
const chLoading = ref(false)
const chDlg = reactive({
  visible: false, isEdit: false, id: null, saving: false,
  form: { name: '', type: 'email', enabled: true, config: {} },
})

const records = ref([])
const recTotal = ref(0)
const recLoading = ref(false)

function statusLabel(s) {
  return { success: '成功', failed: '失败', running: '运行中', waiting: '等待', skipped: '跳过' }[s] || (s || '-')
}
function triggerTag(t) {
  return { manual: 'primary', scheduled: 'warning', rerun: 'danger' }[t] || 'info'
}
function triggerLabel(t) {
  return { manual: '手动', scheduled: '调度', rerun: '重跑' }[t] || (t || '-')
}
function channelSummary(row) {
  const c = row.config || {}
  if (row.type === 'email') return `to: ${c.to || '-'} | smtp: ${c.smtp_host || '-'}:${c.smtp_port || '-'}`
  return `url: ${c.url || '-'}`
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

/* ---------- 告警通道 ---------- */
async function loadChannels() {
  chLoading.value = true
  try {
    channels.value = await http.get('/api/sched/alert-channels')
  } catch { /* ignore */ } finally {
    chLoading.value = false
  }
}

function openChannel(row) {
  chDlg.isEdit = !!row
  chDlg.id = row?.id || null
  chDlg.form = {
    name: row?.name || '',
    type: row?.type || 'email',
    enabled: row ? !!row.enabled : true,
    config: row ? { ...(row.config || {}) } : { to: '', smtp_host: '', smtp_port: 465, username: '', password: '', url: '', secret: '' },
  }
  chDlg.visible = true
}

async function saveChannel() {
  const f = chDlg.form
  if (!f.name.trim()) {
    ElMessage.warning('请填写通道名称')
    return
  }
  const config = {}
  if (f.type === 'email') {
    if (!f.config.to?.trim() || !f.config.smtp_host?.trim()) {
      ElMessage.warning('请填写收件人与 SMTP 主机')
      return
    }
    Object.assign(config, {
      to: f.config.to, smtp_host: f.config.smtp_host,
      smtp_port: f.config.smtp_port || 465, username: f.config.username || '', password: f.config.password || '',
    })
  } else {
    if (!f.config.url?.trim()) {
      ElMessage.warning('请填写 Webhook URL')
      return
    }
    Object.assign(config, { url: f.config.url, secret: f.config.secret || '' })
  }
  chDlg.saving = true
  try {
    const payload = { name: f.name.trim(), type: f.type, config, enabled: f.enabled }
    if (chDlg.isEdit) await http.put(`/api/sched/alert-channels/${chDlg.id}`, payload)
    else await http.post('/api/sched/alert-channels', payload)
    ElMessage.success('已保存')
    chDlg.visible = false
    loadChannels()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    chDlg.saving = false
  }
}

async function removeChannel(row) {
  try {
    await ElMessageBox.confirm(`删除告警通道「${row.name}」？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/sched/alert-channels/${row.id}`)
    ElMessage.success('已删除')
    loadChannels()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

/* ---------- 告警记录 ---------- */
async function loadRecords() {
  recLoading.value = true
  try {
    const res = await http.get('/api/sched/alert-records', { params: { page: 1, size: 50 } })
    records.value = res.items
    recTotal.value = res.total
  } catch { /* ignore */ } finally {
    recLoading.value = false
  }
}

function onTab(name) {
  if (name === 'channels' && !channels.value.length) loadChannels()
  if (name === 'records' && !records.value.length) loadRecords()
}

onMounted(() => {
  // 支持 ?workflow_id= 快速过滤
  const wf = Number(route.query.workflow_id || 0)
  if (wf) instQ.workflow_id = wf
  loadWorkflows()
  loadInstances(1)
  loadChannels()
})
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
</style>
