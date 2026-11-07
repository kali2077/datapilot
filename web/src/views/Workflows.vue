<template>
  <div>
    <div class="page-card">
      <div class="toolbar">
        <h3 class="page-title" style="margin: 0"><el-icon><SetUp /></el-icon>工作流开发</h3>
        <div class="spacer" />
        <el-button icon="Refresh" circle @click="load" />
        <el-button icon="ChatDotRound" @click="openAi('帮我创建一个 ETL 任务，例如：基于 ods_orders 做 dwd 订单明细，剔除测试账号，每天凌晨3点跑')">AI 建 ETL</el-button>
        <el-button type="primary" icon="Plus" @click="openCreate">新建工作流</el-button>
      </div>

      <el-table v-loading="loading" :data="list" stripe>
        <el-table-column prop="name" label="名称" min-width="180">
          <template #default="{ row }"><span style="font-weight: 600">{{ row.name }}</span></template>
        </el-table-column>
        <el-table-column label="类型" width="90">
          <template #default="{ row }">
            <el-tag :type="row.job_type === 'sync' ? 'primary' : 'warning'" size="small">{{ row.job_type }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="schedule_cron" label="Cron" width="130" class-name="mono">
          <template #default="{ row }">{{ row.schedule_cron || '手动' }}</template>
        </el-table-column>
        <el-table-column label="启用" width="90">
          <template #default="{ row }">
            <el-switch :model-value="row.enabled" @change="(v) => toggleEnabled(row, v)" />
          </template>
        </el-table-column>
        <el-table-column label="依赖" min-width="140">
          <template #default="{ row }">{{ depNames(row.depends_on) }}</template>
        </el-table-column>
        <el-table-column prop="next_fire" label="下次执行" width="170">
          <template #default="{ row }">
            <span :class="{ muted: true }">{{ row.next_fire || '未启用' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="330" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openDetail(row)">详情</el-button>
            <el-button link type="success" @click="trigger(row)">立即触发</el-button>
            <el-button link type="primary" @click="$router.push(`/ops?workflow_id=${row.id}`)">实例</el-button>
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="danger" @click="removeWf(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无工作流" /></template>
      </el-table>
    </div>

    <!-- 新建/编辑 -->
    <el-dialog
      v-model="dlg.visible" :title="dlg.isEdit ? '编辑工作流' : '新建工作流'" width="840px" top="4vh"
      destroy-on-close :close-on-click-modal="false"
    >
      <el-form :model="form" label-width="90px">
        <el-row :gutter="14">
          <el-col :span="8">
            <el-form-item label="名称" required><el-input v-model="form.name" /></el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="Cron">
              <el-input v-model="form.schedule_cron" class="mono" placeholder="0 2 * * *（空=手动）" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="依赖">
              <el-select v-model="form.depends_on" multiple clearable placeholder="依赖的工作流" style="width: 100%">
                <el-option v-for="w in list.filter((x) => x.id !== dlg.id)" :key="w.id" :label="w.name" :value="w.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述">
          <el-input v-model="form.description" placeholder="工作流说明（可选）" />
        </el-form-item>
        <el-form-item v-if="!dlg.isEdit" label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>

        <template v-if="!dlg.isEdit">
          <el-divider content-position="left">节点列表（按 DAG 编排）</el-divider>
          <div v-for="(n, i) in form.nodes" :key="n.key" class="node-box">
            <div class="node-head">
              <el-tag size="small" type="info">节点 {{ i + 1 }}</el-tag>
              <el-input v-model="n.name" placeholder="节点名称" style="width: 180px" size="small" />
              <el-select v-model="n.node_type" size="small" style="width: 110px">
                <el-option label="SQL" value="sql" />
                <el-option label="Python" value="python" />
                <el-option label="DataX" value="datax" />
              </el-select>
              <el-select v-model="n.upstream" multiple collapse-tags size="small" placeholder="上游节点" style="width: 200px">
                <el-option
                  v-for="m in form.nodes.filter((x, jx) => jx !== i && x.name)" :key="m.key"
                  :label="m.name" :value="m.name"
                />
              </el-select>
              <div style="flex: 1" />
              <el-button size="small" icon="Delete" circle type="danger" plain @click="removeNode(i)" />
            </div>

            <!-- sql 节点 -->
            <template v-if="n.node_type === 'sql'">
              <el-row :gutter="12">
                <el-col :span="10">
                  <el-select v-model="n.config.datasource_id" filterable placeholder="选择数据源" size="small" style="width: 100%">
                    <el-option v-for="d in dsList" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
                  </el-select>
                </el-col>
                <el-col :span="10">
                  <el-input v-model="n.config.target_table" placeholder="目标表" size="small" />
                </el-col>
                <el-col :span="4" style="display: flex; gap: 6px">
                  <el-button size="small" :loading="n.checking" @click="checkSql(n)">校验</el-button>
                  <el-button size="small" type="primary" plain :loading="n.explaining" @click="explainSql(n)">EXPLAIN</el-button>
                </el-col>
              </el-row>
              <el-input
                v-model="n.config.sql" type="textarea" :rows="4" size="small" class="mono"
                placeholder="INSERT INTO 目标表 SELECT ... （ETL 语句）" style="margin-top: 8px"
              />
              <template v-if="n.check">
                <el-alert
                  style="margin-top: 8px" :closable="false" show-icon
                  :type="n.check.etl_ok && n.check.readonly_ok === false ? 'warning' : n.check.etl_ok ? 'success' : 'error'"
                  :title="`ETL 语法: ${n.check.etl_ok ? '通过' : '不通过'} ${n.check.etl_msg || ''}`"
                  :description="n.check.readonly_ok === false ? `只读检查: ${n.check.readonly_msg || '失败'}` : ''"
                />
                <div v-if="n.check.sources && n.check.sources.length" class="muted" style="margin-top: 4px">
                  识别到来源表: <span class="mono">{{ n.check.sources.join(', ') }}</span>
                  <template v-if="n.check.target"> ｜ 目标: <span class="mono">{{ n.check.target }}</span></template>
                </div>
              </template>
              <pre v-if="n.plan" class="sql-pre" style="margin-top: 8px">{{ n.plan.join('\n') }}</pre>
            </template>

            <!-- python 节点 -->
            <template v-else-if="n.node_type === 'python'">
              <el-input
                v-model="n.config.script" type="textarea" :rows="5" size="small" class="mono"
                placeholder="Python 脚本（可访问 context）"
              />
            </template>

            <!-- datax 节点 -->
            <template v-else>
              <el-select v-model="n.config.sync_job_id" filterable placeholder="选择集成任务（同步）" style="width: 320px">
                <el-option v-for="j in integJobs" :key="j.id" :label="`${j.name}（${j.source?.name || '?'} → ${j.target?.name || '?'}）`" :value="j.id" />
              </el-select>
            </template>
          </div>
          <el-button icon="Plus" @click="addNode">添加节点</el-button>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">{{ dlg.isEdit ? '保存' : '创建' }}</el-button>
      </template>
    </el-dialog>

    <!-- 详情抽屉 -->
    <el-drawer v-model="detailDlg.visible" :title="`工作流：${detailDlg.info?.name || ''}`" size="640px">
      <template v-if="detailDlg.info">
        <el-descriptions :column="2" size="small" border style="margin-bottom: 16px">
          <el-descriptions-item label="类型">{{ detailDlg.info.job_type }}</el-descriptions-item>
          <el-descriptions-item label="Cron">{{ detailDlg.info.schedule_cron || '手动' }}</el-descriptions-item>
          <el-descriptions-item label="启用">
            <el-tag :type="detailDlg.info.enabled ? 'success' : 'info'" size="small">{{ detailDlg.info.enabled ? '是' : '否' }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="下次执行">{{ detailDlg.info.next_fire || '-' }}</el-descriptions-item>
          <el-descriptions-item label="依赖" :span="2">{{ depNames(detailDlg.info.depends_on) || '无' }}</el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">{{ detailDlg.info.description || '-' }}</el-descriptions-item>
        </el-descriptions>

        <h4 style="margin: 4px 0 8px">节点列表（{{ detailDlg.info.nodes?.length || 0 }}）</h4>
        <el-table :data="detailDlg.info.nodes || []" size="small" border>
          <el-table-column prop="name" label="节点" min-width="130" />
          <el-table-column label="类型" width="80">
            <template #default="{ row }"><el-tag size="small" effect="plain">{{ row.node_type }}</el-tag></template>
          </el-table-column>
          <el-table-column label="配置摘要" min-width="180" show-overflow-tooltip>
            <template #default="{ row }">{{ configSummary(row) }}</template>
          </el-table-column>
          <el-table-column label="上游" min-width="120">
            <template #default="{ row }">{{ upstreamNames(row) || '-' }}</template>
          </el-table-column>
        </el-table>

        <div style="margin-top: 18px; display: flex; gap: 10px">
          <el-button type="success" icon="VideoPlay" @click="trigger(detailDlg.info)">立即触发</el-button>
          <el-button icon="List" @click="$router.push(`/ops?workflow_id=${detailDlg.info.id}`); detailDlg.visible = false">查看实例</el-button>
        </div>
      </template>
      <div v-else v-loading="true" style="height: 300px" />
    </el-drawer>

    <!-- SQL 校验问题（创建返回 problems） -->
    <el-dialog v-model="problemsDlg.visible" title="工作流已创建，但存在以下 SQL 校验问题" width="520px">
      <el-alert
        v-for="(p, i) in problemsDlg.list" :key="i" :title="p" type="warning" show-icon :closable="false"
        style="margin-bottom: 8px"
      />
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import { openAi } from '../ai'

const list = ref([])
const dsList = ref([])
const integJobs = ref([])
const loading = ref(false)
const saving = ref(false)
let nodeKeySeq = 1

const dlg = reactive({ visible: false, isEdit: false, id: null })
const form = reactive({
  name: '', description: '', schedule_cron: '', depends_on: [], enabled: true, nodes: [],
})

const detailDlg = reactive({ visible: false, info: null })
const problemsDlg = reactive({ visible: false, list: [] })

function depNames(ids) {
  if (!ids || !ids.length) return '无'
  return ids.map((id) => list.value.find((w) => w.id === id)?.name || `#${id}`).join('、')
}

async function load() {
  loading.value = true
  try {
    list.value = await http.get('/api/sched/workflows')
  } catch { /* ignore */ } finally {
    loading.value = false
  }
}

async function loadRefs() {
  try {
    dsList.value = await http.get('/api/datasources')
  } catch { /* ignore */ }
  try {
    integJobs.value = await http.get('/api/integ/jobs')
  } catch { /* ignore */ }
}

function addNode() {
  form.nodes.push({
    key: `n${nodeKeySeq++}`,
    name: '',
    node_type: 'sql',
    config: { datasource_id: null, target_table: '', sql: '', script: '', sync_job_id: null },
    upstream: [],
    check: null,
    plan: null,
    checking: false,
    explaining: false,
  })
}

function removeNode(i) {
  const removed = form.nodes[i]
  form.nodes.splice(i, 1)
  form.nodes.forEach((n) => {
    n.upstream = (n.upstream || []).filter((k) => k !== removed.name)
  })
}

function openCreate() {
  dlg.isEdit = false
  dlg.id = null
  Object.assign(form, { name: '', description: '', schedule_cron: '', depends_on: [], enabled: true, nodes: [] })
  addNode()
  dlg.visible = true
}

function openEdit(row) {
  dlg.isEdit = true
  dlg.id = row.id
  Object.assign(form, {
    name: row.name,
    description: row.description || '',
    schedule_cron: row.schedule_cron || '',
    depends_on: [...(row.depends_on || [])],
    enabled: !!row.enabled,
    nodes: [],
  })
  dlg.visible = true
}

async function checkSql(n) {
  if (!n.config.datasource_id || !n.config.sql?.trim()) {
    ElMessage.warning('请先选择数据源并填写 SQL')
    return
  }
  n.checking = true
  try {
    n.check = await http.post('/api/sched/dev/sql/validate', {
      datasource_id: n.config.datasource_id,
      sql: n.config.sql,
      target_table: n.config.target_table || '',
    })
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    n.checking = false
  }
}

async function explainSql(n) {
  if (!n.config.datasource_id || !n.config.sql?.trim()) {
    ElMessage.warning('请先选择数据源并填写 SQL')
    return
  }
  n.explaining = true
  try {
    const res = await http.post('/api/sched/dev/sql/explain', {
      datasource_id: n.config.datasource_id,
      sql: n.config.sql,
      target_table: n.config.target_table || '',
    })
    n.plan = res.plan || []
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    n.explaining = false
  }
}

async function save() {
  if (!form.name.trim()) {
    ElMessage.warning('请填写工作流名称')
    return
  }
  if (!dlg.isEdit) {
    const named = form.nodes.filter((n) => n.name.trim())
    if (!named.length) {
      ElMessage.warning('请至少配置一个节点（含名称）')
      return
    }
    const payload = {
      name: form.name.trim(),
      description: form.description,
      schedule_cron: form.schedule_cron.trim(),
      depends_on: form.depends_on,
      enabled: form.enabled,
      nodes: named.map((n) => {
        let config = {}
        if (n.node_type === 'sql') {
          config = {
            datasource_id: n.config.datasource_id,
            target_table: n.config.target_table || '',
            sql: n.config.sql || '',
          }
        } else if (n.node_type === 'python') {
          config = { script: n.config.script || '' }
        } else {
          config = { sync_job_id: n.config.sync_job_id }
        }
        return {
          name: n.name.trim(),
          node_type: n.node_type,
          config,
          upstream: [...(n.upstream || [])],
        }
      }),
    }
    saving.value = true
    try {
      const res = await http.post('/api/sched/workflows', payload)
      dlg.visible = false
      if (res.problems && res.problems.length) {
        problemsDlg.list = res.problems
        problemsDlg.visible = true
      } else {
        ElMessage.success('工作流已创建')
      }
      load()
    } catch (e) {
      ElMessage.error(errMsg(e))
    } finally {
      saving.value = false
    }
  } else {
    saving.value = true
    try {
      await http.put(`/api/sched/workflows/${dlg.id}`, {
        name: form.name.trim(),
        description: form.description,
        schedule_cron: form.schedule_cron.trim(),
        depends_on: form.depends_on,
        enabled: form.enabled,
      })
      ElMessage.success('已保存')
      dlg.visible = false
      load()
    } catch (e) {
      ElMessage.error(errMsg(e))
    } finally {
      saving.value = false
    }
  }
}

async function toggleEnabled(row, v) {
  try {
    await http.put(`/api/sched/workflows/${row.id}`, { enabled: v })
    row.enabled = v
    ElMessage.success(v ? '已启用调度' : '已暂停调度')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function removeWf(row) {
  let force = false
  try {
    await ElMessageBox.confirm(
      `删除工作流「${row.name}」？${row.enabled ? '工作流启用中，' : ''}连同节点与血缘一并删除（已有实例需强制删除）。`,
      '删除确认', { type: 'warning', confirmButtonText: '删除' })
    if (row.enabled) force = true
  } catch { return }
  try {
    await http.delete(`/api/sched/workflows/${row.id}`, { params: force ? { force: 'true' } : {} })
    ElMessage.success('已删除')
    load()
  } catch (e) {
    // 启用中/有实例 → 后端 400 提示，此处自动带 force 重试一次
    if (row.enabled || /实例/.test(errMsg(e))) {
      try {
        await http.delete(`/api/sched/workflows/${row.id}`, { params: { force: 'true' } })
        ElMessage.success('已强制删除')
        load()
        return
      } catch (e2) {
        ElMessage.error(errMsg(e2))
        return
      }
    }
    ElMessage.error(errMsg(e))
  }
}

async function openDetail(row) {
  detailDlg.info = null
  detailDlg.visible = true
  try {
    detailDlg.info = await http.get(`/api/sched/workflows/${row.id}`)
  } catch { /* ignore */ }
}

function configSummary(node) {
  const c = node.config || {}
  if (node.node_type === 'sql') {
    return `数据源 #${c.datasource_id || '-'} → ${c.target_table || '-'} ｜ ${(c.sql || '').slice(0, 60)}`
  }
  if (node.node_type === 'python') return `script: ${(c.script || '').slice(0, 60) || '(空)'}`
  return `同步任务 #${c.sync_job_id || '-'}`
}

function upstreamNames(node) {
  if (!node.upstream || !node.upstream.length) return ''
  const nodes = detailDlg.info?.nodes || []
  return node.upstream
    .map((u) => nodes.find((n) => n.id === u || n.key === u)?.name || (typeof u === 'string' ? u : `#${u}`))
    .join('、')
}

async function trigger(row) {
  try {
    await ElMessageBox.confirm(`立即触发工作流「${row.name}」？`, '触发确认', { type: 'info' })
  } catch { return }
  try {
    const res = await http.post(`/api/sched/workflows/${row.id}/trigger`)
    ElMessage.success(`已触发，实例 #${res.instance_id}（${res.status}）`)
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(() => {
  load()
  loadRefs()
})
</script>

<style scoped>
.node-box {
  border: 1px solid var(--dp-border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  background: var(--dp-bg-subtle);
}
.node-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
</style>
