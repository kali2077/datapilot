<template>
  <div>
    <el-tabs v-model="tab" class="ds-tabs">
      <el-tab-pane label="数据源" name="datasources">
    <div class="page-card">
      <div class="toolbar">
        <h3 class="page-title" style="margin: 0"><el-icon><Coin /></el-icon>数据源管理</h3>
        <div class="spacer" />
        <el-button :icon="'Refresh'" circle @click="load" />
        <el-button v-if="auth.canWrite" type="primary" icon="Plus" @click="openCreate">新建数据源</el-button>
      </div>

      <el-table v-loading="loading" :data="list" stripe>
        <el-table-column prop="name" label="名称" min-width="140">
          <template #default="{ row }">
            <span style="font-weight: 600">{{ row.name }}</span>
            <el-tag v-if="row.status === 'active'" type="success" size="small" style="margin-left: 8px">正常</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="type" label="类型" width="110">
          <template #default="{ row }">
            <el-tag :type="typeTag(row.type)" effect="plain">{{ row.type.toUpperCase() }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="主机" min-width="170">
          <template #default="{ row }">
            <span v-if="row.type === 'sqlite'" class="mono muted">{{ row.params?.path || '(sqlite 文件)' }}</span>
            <span v-else class="mono">{{ row.host }}:{{ row.port }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="database" label="数据库" min-width="120" />
        <el-table-column prop="description" label="描述" min-width="160" show-overflow-tooltip>
          <template #default="{ row }">{{ row.description || '-' }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="170">
          <template #default="{ row }"><span class="muted">{{ row.updated_at || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="testConn(row)">测试连接</el-button>
            <el-button link type="primary" :loading="collectingId === row.id" @click="collect(row)">采集元数据</el-button>
            <el-button v-if="auth.canWrite" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button v-if="auth.canWrite" link type="danger" @click="remove(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无数据源" /></template>
      </el-table>
    </div>

    <!-- 新建/编辑 -->
    <el-dialog v-model="dlg.visible" :title="dlg.isEdit ? '编辑数据源' : '新建数据源'" width="560px" destroy-on-close>
      <el-form :model="form" label-width="90px">
        <el-form-item label="类型" required>
          <el-select v-model="form.type" :disabled="dlg.isEdit" style="width: 100%" @change="onTypeChange">
            <el-option v-for="t in TYPES" :key="t.v" :label="t.l" :value="t.v" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称" required>
          <el-input v-model="form.name" placeholder="数据源名称" />
        </el-form-item>

        <template v-if="form.type === 'sqlite'">
          <el-form-item label="文件路径" required>
            <el-input v-model="form.path" placeholder="/path/to/database.db" class="mono" />
          </el-form-item>
        </template>
        <template v-else>
          <el-row :gutter="12">
            <el-col :span="14">
              <el-form-item label="主机" required>
                <el-input v-model="form.host" placeholder="127.0.0.1" />
              </el-form-item>
            </el-col>
            <el-col :span="10">
              <el-form-item label="端口" required>
                <el-input-number v-model="form.port" :min="1" :max="65535" style="width: 100%" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="12">
            <el-col :span="14">
              <el-form-item label="数据库" required>
                <el-input v-model="form.database" />
              </el-form-item>
            </el-col>
            <el-col :span="10">
              <el-form-item label="用户名">
                <el-input v-model="form.username" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="密码">
            <el-input v-model="form.password" type="password" show-password
              :placeholder="dlg.isEdit && form.has_password ? '留空则不修改密码' : '密码'" />
          </el-form-item>
        </template>

        <template v-if="form.type === 'maxcompute'">
          <el-form-item label="Endpoint" required>
            <el-input v-model="form.params_endpoint" class="mono"
              placeholder="http://service.cn-hangzhou.maxcompute.aliyun.com/api" />
          </el-form-item>
          <el-form-item label="Partition">
            <el-input v-model="form.params_partition" class="mono" placeholder="可选分区，如 pt=20261104" />
          </el-form-item>
        </template>

        <el-form-item label="描述">
          <el-input v-model="form.description" type="textarea" :rows="2" placeholder="用途说明（可选）" />
        </el-form-item>
        <el-form-item v-if="form.type !== 'sqlite'" label="扩展参数">
          <el-input v-model="form.paramsText" type="textarea" :rows="2" class="mono"
            placeholder='JSON，可空，如 {"charset":"utf8mb4"}' />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>
      </el-tab-pane>
      <el-tab-pane label="Hadoop 平台" name="hadoop">
        <Hadoop />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import { useAuthStore } from '../store'
import Hadoop from './Hadoop.vue'

const auth = useAuthStore()
const list = ref([])
const loading = ref(false)
const saving = ref(false)
const collectingId = ref(null)

const tab = ref('datasources')
const dlg = reactive({ visible: false, isEdit: false, id: null })
const form = reactive({
  type: 'mysql', name: '', host: '127.0.0.1', port: 3306, database: '',
  username: '', password: '', has_password: false, path: '', description: '', paramsText: '',
  params_endpoint: '', params_partition: '',
})

const TYPES = [
  { v: 'mysql', l: 'MySQL' }, { v: 'doris', l: 'Doris' },
  { v: 'starrocks', l: 'StarRocks' }, { v: 'oracle', l: 'Oracle' },
  { v: 'clickhouse', l: 'ClickHouse' }, { v: 'postgres', l: 'PostgreSQL' },
  { v: 'hive', l: 'Hive' }, { v: 'spark', l: 'Spark (Thrift)' },
  { v: 'trino', l: 'Trino' }, { v: 'maxcompute', l: 'MaxCompute (ODPS)' },
  { v: 'mssql', l: 'SQL Server' },
  { v: 'sqlite', l: 'SQLite' },
]

function typeTag(t) {
  return { mysql: 'primary', doris: 'warning', starrocks: 'warning', oracle: 'danger',
           clickhouse: 'danger', postgres: 'success', sqlite: 'info',
           hive: 'warning', spark: 'warning', trino: 'primary', maxcompute: 'danger', mssql: 'danger' }[t] || 'info'
}

const DEFAULT_PORT = { mysql: 3306, doris: 9030, starrocks: 9030, oracle: 1521,
                       clickhouse: 8123, postgres: 5432, sqlite: 0,
                       hive: 10000, spark: 10001, trino: 8080, maxcompute: 0, mssql: 1433 }

function onTypeChange() {
  if (DEFAULT_PORT[form.type]) form.port = DEFAULT_PORT[form.type]
}

async function load() {
  loading.value = true
  try {
    list.value = await http.get('/api/datasources')
  } catch (e) {
    /* 拦截器已提示 */
  } finally {
    loading.value = false
  }
}

function openCreate() {
  dlg.isEdit = false
  dlg.id = null
  Object.assign(form, {
    type: 'mysql', name: '', host: '127.0.0.1', port: 3306, database: '',
    username: '', password: '', has_password: false, path: '', description: '', paramsText: '',
    params_endpoint: '', params_partition: '',
  })
  dlg.visible = true
}

function openEdit(row) {
  dlg.isEdit = true
  dlg.id = row.id
  Object.assign(form, {
    type: row.type, name: row.name, host: row.host, port: row.port || 0, database: row.database,
    username: row.username, password: '', has_password: row.has_password,
    path: row.params?.path || '', description: row.description || '',
    params_endpoint: row.params?.endpoint || '', params_partition: row.params?.partition || '',
    paramsText: row.params && Object.keys(row.params).length &&
      !row.params.path && !row.params.endpoint && !row.params.partition
      ? JSON.stringify(row.params, null, 0) : '',
  })
  dlg.visible = true
}

function buildPayload() {
  const payload = {
    name: form.name.trim(),
    type: form.type,
    host: form.host || '',
    port: Number(form.port) || 0,
    database: form.database || '',
    username: form.username || '',
    password: form.password || '',
    params: {},
    description: form.description || '',
  }
  if (form.type === 'sqlite') {
    payload.params = { path: form.path.trim() }
  } else if (form.type === 'maxcompute') {
    payload.params = {}
    if (form.params_endpoint.trim()) payload.params.endpoint = form.params_endpoint.trim()
    if (form.params_partition.trim()) payload.params.partition = form.params_partition.trim()
  } else if (form.paramsText && form.paramsText.trim()) {
    try {
      payload.params = JSON.parse(form.paramsText)
    } catch {
      ElMessage.error('扩展参数不是合法 JSON')
      return null
    }
  }
  if (!payload.name) {
    ElMessage.warning('请填写数据源名称')
    return null
  }
  if (form.type === 'sqlite' && !payload.params.path) {
    ElMessage.warning('请填写 SQLite 文件路径')
    return null
  }
  return payload
}

async function save() {
  const payload = buildPayload()
  if (!payload) return
  saving.value = true
  try {
    if (dlg.isEdit) {
      await http.put(`/api/datasources/${dlg.id}`, payload)
      ElMessage.success('已保存')
    } else {
      await http.post('/api/datasources', payload)
      ElMessage.success('数据源已创建')
    }
    dlg.visible = false
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    saving.value = false
  }
}

async function testConn(row) {
  try {
    const res = await http.post(`/api/datasources/${row.id}/test`)
    if (res.ok) ElMessage.success(`连接成功：${res.message || row.name}`)
    else ElMessage.error(`连接失败：${res.message || '未知错误'}`)
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function collect(row) {
  collectingId.value = row.id
  try {
    const res = await http.post(`/api/datasources/${row.id}/collect`)
    ElMessageBox.alert(
      `<div style="display:flex;gap:28px;justify-content:center;padding:6px 0">
        <div style="text-align:center"><div style="font-size:26px;font-weight:700;color:var(--dp-accent)">${res.tables}</div><div class="muted">表</div></div>
        <div style="text-align:center"><div style="font-size:26px;font-weight:700;color:var(--dp-ok)">${res.columns}</div><div class="muted">字段</div></div>
      </div>`,
      `采集完成：${row.name}`,
      { dangerouslyUseHTMLString: true, confirmButtonText: '知道了' }
    ).catch(() => {})
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    collectingId.value = null
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`确定删除数据源「${row.name}」吗？相关元数据与任务配置可能受影响。`, '删除确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await http.delete(`/api/datasources/${row.id}`)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(load)
</script>
