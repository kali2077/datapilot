<template>
  <div>
    <div class="page-card">
      <div class="toolbar">
        <span class="muted">注册 Hadoop 集群（HDFS/Hive/Spark/Trino 端点 + Kerberos），一键派生平台数据源</span>
        <div class="spacer" />
        <el-button type="primary" icon="Plus" @click="openDlg()">注册平台</el-button>
        <el-button icon="Refresh" circle @click="load" />
      </div>
      <el-table :data="list" stripe v-loading="loading">
        <el-table-column prop="name" label="平台名称" min-width="140" />
        <el-table-column label="组件端点" min-width="300">
          <template #default="{ row }">
            <div class="mono tiny" style="line-height: 1.6">
              <div v-if="row.hdfs_uri">HDFS: {{ row.hdfs_uri }}</div>
              <div v-if="row.hs2_uri">Hive: {{ row.hs2_uri }}</div>
              <div v-if="row.spark_uri">Spark: {{ row.spark_uri }}</div>
              <div v-if="row.trino_uri">Trino: {{ row.trino_uri }}</div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="Kerberos" width="100">
          <template #default="{ row }">
            <el-tag v-if="row.kerberos?.enabled" size="small" type="danger" effect="plain">启用</el-tag>
            <span v-else class="muted">-</span>
          </template>
        </el-table-column>
        <el-table-column prop="description" label="描述" min-width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button link type="success" @click="testCluster(row)">连通测试</el-button>
            <el-button link type="primary" @click="openDerive(row)">派生数据源</el-button>
            <el-button link type="warning" @click="openKeytab(row)" v-if="row.kerberos?.enabled">Keytab</el-button>
            <el-button link type="primary" @click="openDlg(row)">编辑</el-button>
            <el-button link type="danger" @click="removeCluster(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无 Hadoop 平台，点击右上角注册" /></template>
      </el-table>
    </div>

    <!-- 注册/编辑 -->
    <el-dialog v-model="dlg.visible" :title="dlg.isEdit ? '编辑 Hadoop 平台' : '注册 Hadoop 平台'" width="720px" destroy-on-close>
      <el-form :model="form" label-width="150px">
        <el-form-item label="平台名称" required>
          <el-input v-model="form.name" placeholder="例如：生产 Hadoop 集群" />
        </el-form-item>
        <el-form-item label="HDFS (WebHDFS)">
          <el-input v-model="form.hdfs_uri" class="mono" placeholder="http://namenode:9870" />
        </el-form-item>
        <el-form-item label="HiveServer2 (thrift)">
          <el-input v-model="form.hs2_uri" class="mono" placeholder="thrift://hiveserver2:10000" />
        </el-form-item>
        <el-form-item label="Metastore (thrift)">
          <el-input v-model="form.metastore_uri" class="mono" placeholder="thrift://metastore:9083" />
        </el-form-item>
        <el-form-item label="Spark (thrift)">
          <el-input v-model="form.spark_uri" class="mono" placeholder="thrift://spark:10001" />
        </el-form-item>
        <el-form-item label="Trino (http)">
          <el-input v-model="form.trino_uri" class="mono" placeholder="http://trino:8080" />
        </el-form-item>
        <el-form-item label="Trino Catalog">
          <el-input v-model="form.trino_catalog" class="mono" placeholder="例如 hive" />
        </el-form-item>
        <el-form-item label="Kerberos">
          <el-switch v-model="form.kerberos_enabled" />
          <span class="muted tiny" style="margin-left: 10px">启用后派生数据源默认走 Kerberos 认证</span>
        </el-form-item>
        <template v-if="form.kerberos_enabled">
          <el-form-item label="Realm">
            <el-input v-model="form.kerberos_realm" class="mono" placeholder="EXAMPLE.COM" />
          </el-form-item>
          <el-form-item label="Principal">
            <el-input v-model="form.kerberos_principal" class="mono" placeholder="hive/_HOST@EXAMPLE.COM" />
          </el-form-item>
        </template>
        <el-form-item label="描述">
          <el-input v-model="form.description" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="dlg.saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 派生数据源 -->
    <el-dialog v-model="deriveDlg.visible" title="派生数据源" width="520px" destroy-on-close>
      <el-form label-width="110px">
        <el-form-item label="平台">
          <el-tag>{{ deriveDlg.cluster?.name }}</el-tag>
        </el-form-item>
        <el-form-item label="类型" required>
          <el-radio-group v-model="deriveDlg.kind">
            <el-radio-button value="hive" :disabled="!deriveDlg.cluster?.hs2_uri">Hive</el-radio-button>
            <el-radio-button value="spark" :disabled="!deriveDlg.cluster?.spark_uri">Spark</el-radio-button>
            <el-radio-button value="trino" :disabled="!deriveDlg.cluster?.trino_uri">Trino</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="数据源名称" required>
          <el-input v-model="deriveDlg.name" :placeholder="`例如 ${deriveDlg.cluster?.name || ''}_hive`" />
        </el-form-item>
        <el-form-item label="数据库/Catalog">
          <el-input v-model="deriveDlg.database" :placeholder="deriveDlg.kind === 'trino' ? 'catalog.schema（可留空，用平台 catalog）' : '数据库名，可留空'" />
        </el-form-item>
        <el-form-item label="用户名">
          <el-input v-model="deriveDlg.username" placeholder="Kerberos 启用时可不填" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input v-model="deriveDlg.password" type="password" placeholder="LDAP/密码认证时填" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="deriveDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="deriveDlg.saving" @click="doDerive">派生</el-button>
      </template>
    </el-dialog>

    <!-- 连通测试结果 -->
    <el-dialog v-model="testDlg.visible" :title="`连通测试：${testDlg.name}`" width="520px">
      <el-descriptions v-for="(v, k) in testDlg.results" :key="k" :column="1" size="small" border style="margin-bottom: 10px" :title="String(k)">
        <el-descriptions-item label="状态">
          <el-tag v-if="v.ok === true" size="small" type="success" effect="dark">OK</el-tag>
          <el-tag v-else-if="v.ok === false" size="small" type="danger" effect="dark">失败</el-tag>
          <el-tag v-else size="small" type="info">未配置</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="信息">{{ v.message }}</el-descriptions-item>
      </el-descriptions>
    </el-dialog>

    <!-- Keytab 上传 -->
    <el-dialog v-model="keytabDlg.visible" title="上传 Keytab" width="420px">
      <el-upload drag :auto-upload="false" :limit="1" :on-change="onKeytabPick" :file-list="keytabDlg.files">
        <el-icon size="40" color="#98a2b3"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽 .keytab 文件到此，或点击选择（&lt;2MB）</div>
      </el-upload>
      <template #footer>
        <el-button @click="keytabDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="keytabDlg.saving" :disabled="!keytabDlg.file" @click="doUploadKeytab">上传</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'

const list = ref([])
const loading = ref(false)

const dlg = reactive({ visible: false, isEdit: false, id: null, saving: false })
const form = reactive({
  name: '', hdfs_uri: '', hs2_uri: '', metastore_uri: '', spark_uri: '',
  trino_uri: '', trino_catalog: '', kerberos_enabled: false,
  kerberos_realm: '', kerberos_principal: '', description: '',
})

const deriveDlg = reactive({ visible: false, cluster: null, kind: 'hive', name: '', database: '', username: '', password: '', saving: false })
const testDlg = reactive({ visible: false, name: '', results: {} })
const keytabDlg = reactive({ visible: false, cluster: null, file: null, files: [], saving: false })

async function load() {
  loading.value = true
  try {
    list.value = await http.get('/api/hadoop/clusters')
  } finally {
    loading.value = false
  }
}

function openDlg(row) {
  dlg.isEdit = !!row
  dlg.id = row?.id || null
  Object.assign(form, {
    name: row?.name || '', hdfs_uri: row?.hdfs_uri || '', hs2_uri: row?.hs2_uri || '',
    metastore_uri: row?.metastore_uri || '', spark_uri: row?.spark_uri || '',
    trino_uri: row?.trino_uri || '', trino_catalog: row?.trino_catalog || '',
    kerberos_enabled: !!row?.kerberos?.enabled, kerberos_realm: row?.kerberos?.realm || '',
    kerberos_principal: row?.kerberos?.principal || '', description: row?.description || '',
  })
  dlg.visible = true
}

async function save() {
  if (!form.name.trim()) { ElMessage.warning('请填写平台名称'); return }
  dlg.saving = true
  try {
    const payload = {
      name: form.name.trim(), hdfs_uri: form.hdfs_uri.trim(), hs2_uri: form.hs2_uri.trim(),
      metastore_uri: form.metastore_uri.trim(), spark_uri: form.spark_uri.trim(),
      trino_uri: form.trino_uri.trim(), trino_catalog: form.trino_catalog.trim(),
      kerberos: { enabled: form.kerberos_enabled, realm: form.kerberos_realm.trim(),
                  principal: form.kerberos_principal.trim(),
                  keytab_name: dlg.isEdit ? (list.value.find(x => x.id === dlg.id)?.kerberos?.keytab_name || '') : '' },
      description: form.description,
    }
    if (dlg.isEdit) await http.put(`/api/hadoop/clusters/${dlg.id}`, payload)
    else await http.post('/api/hadoop/clusters', payload)
    dlg.visible = false
    ElMessage.success(dlg.isEdit ? '已保存' : '平台已注册')
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    dlg.saving = false
  }
}

async function testCluster(row) {
  try {
    const r = await http.post(`/api/hadoop/clusters/${row.id}/test`)
    testDlg.name = row.name
    testDlg.results = r.components
    testDlg.visible = true
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

function openDerive(row) {
  deriveDlg.cluster = row
  deriveDlg.kind = row.hs2_uri ? 'hive' : (row.spark_uri ? 'spark' : 'trino')
  deriveDlg.name = `${row.name}_${deriveDlg.kind}`
  deriveDlg.database = ''
  deriveDlg.username = ''
  deriveDlg.password = ''
  deriveDlg.visible = true
}

async function doDerive() {
  if (!deriveDlg.name.trim()) { ElMessage.warning('请填写数据源名称'); return }
  deriveDlg.saving = true
  try {
    const r = await http.post(`/api/hadoop/clusters/${deriveDlg.cluster.id}/derive`, {
      kind: deriveDlg.kind, name: deriveDlg.name.trim(),
      database: deriveDlg.database, username: deriveDlg.username, password: deriveDlg.password,
    })
    deriveDlg.visible = false
    ElMessage.success(`已派生数据源「${r.name}」（${r.type} @ ${r.host}:${r.port}），可在数据源管理中测试连接`)
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    deriveDlg.saving = false
  }
}

function openKeytab(row) {
  keytabDlg.cluster = row
  keytabDlg.file = null
  keytabDlg.files = []
  keytabDlg.visible = true
}

function onKeytabPick(file) {
  keytabDlg.file = file.raw
}

async function doUploadKeytab() {
  keytabDlg.saving = true
  try {
    const fd = new FormData()
    fd.append('file', keytabDlg.file)
    await http.post(`/api/hadoop/clusters/${keytabDlg.cluster.id}/keytab`, fd,
                    { headers: { 'Content-Type': 'multipart/form-data' } })
    ElMessage.success('keytab 已上传（加密目录存储）')
    keytabDlg.visible = false
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    keytabDlg.saving = false
  }
}

async function removeCluster(row) {
  try {
    await ElMessageBox.confirm(`删除 Hadoop 平台「${row.name}」？派生数据源不受影响但将失去平台关联。`, '删除确认',
                               { type: 'warning', confirmButtonText: '删除' })
  } catch { return }
  try {
    await http.delete(`/api/hadoop/clusters/${row.id}`)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(load)
</script>
