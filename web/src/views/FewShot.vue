<template>
  <div class="page-card">
    <div class="toolbar">
      <span class="muted">问题 → SQL 样例库 —— 智能问数 RAG 的一部分：按问题相似度命中后作为 few-shot 注入，
        引导 AI 生成同口径 SQL</span>
      <div class="spacer" />
      <el-button v-if="auth.canWrite" type="primary" icon="Plus" @click="openFewshot()">新建样例</el-button>
    </div>
    <el-table v-loading="fewshotLoading" :data="fewshot" stripe>
      <el-table-column prop="question" label="问题" min-width="220" show-overflow-tooltip />
      <el-table-column label="SQL" min-width="380">
        <template #default="{ row }">
          <span class="mono tiny">{{ row.sql }}</span>
        </template>
      </el-table-column>
      <el-table-column label="关联数据源" width="140">
        <template #default="{ row }">{{ dsName(row.datasource_id) }}</template>
      </el-table-column>
      <el-table-column label="启用" width="90">
        <template #default="{ row }">
          <el-switch v-if="auth.canWrite" :model-value="row.enabled"
                     @change="(v) => toggleEnabled(row, v)" />
          <el-tag v-else :type="row.enabled ? 'success' : 'info'" size="small">
            {{ row.enabled ? '启用' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column v-if="auth.canWrite" label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openFewshot(row)">编辑</el-button>
          <el-button link type="danger" @click="removeFewshot(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty><el-empty description="暂无样例。例：查一下GMV → SELECT SUM(amount) FROM orders WHERE status='paid'" /></template>
    </el-table>

    <el-dialog v-model="fDlg.visible" :title="fDlg.isEdit ? '编辑样例' : '新建样例'" width="560px" destroy-on-close>
      <el-form :model="fDlg.form" label-width="70px">
        <el-form-item label="问题" required>
          <el-input v-model="fDlg.form.question" placeholder="用户会怎么问，如：查一下GMV" />
        </el-form-item>
        <el-form-item label="SQL" required>
          <el-input v-model="fDlg.form.sql" type="textarea" :rows="5" class="mono"
                    placeholder="期望生成的标准 SQL" />
        </el-form-item>
        <el-form-item label="数据源">
          <el-select v-model="fDlg.form.datasource_id" clearable placeholder="不限（跨源通用）" style="width: 220px">
            <el-option v-for="d in dsList" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="启用"><el-switch v-model="fDlg.form.enabled" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="fDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="fDlg.saving" @click="saveFewshot">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import { useAuthStore } from '../store'

const auth = useAuthStore()
const dsList = ref([])
const fewshot = ref([])
const fewshotLoading = ref(false)
const fDlg = reactive({ visible: false, isEdit: false, id: null, saving: false, form: { question: '', sql: '', datasource_id: null, enabled: true } })

function dsName(id) {
  const d = dsList.value.find((x) => x.id === id)
  return d ? d.name : id ? `#${id}` : '不限'
}

async function loadDs() {
  try {
    dsList.value = await http.get('/api/datasources')
  } catch { /* ignore */ }
}

async function loadFewshot() {
  fewshotLoading.value = true
  try {
    fewshot.value = await http.get('/api/meta/fewshot')
  } catch { /* ignore */ } finally {
    fewshotLoading.value = false
  }
}

function openFewshot(row) {
  fDlg.isEdit = !!row
  fDlg.id = row?.id || null
  fDlg.form = row
    ? { question: row.question, sql: row.sql, datasource_id: row.datasource_id || null, enabled: !!row.enabled }
    : { question: '', sql: '', datasource_id: null, enabled: true }
  fDlg.visible = true
}

async function saveFewshot() {
  if (!fDlg.form.question.trim() || !fDlg.form.sql.trim()) {
    ElMessage.warning('问题与 SQL 均必填')
    return
  }
  fDlg.saving = true
  try {
    const payload = { ...fDlg.form, datasource_id: fDlg.form.datasource_id || 0 }
    if (fDlg.isEdit) await http.put(`/api/meta/fewshot/${fDlg.id}`, payload)
    else await http.post('/api/meta/fewshot', payload)
    ElMessage.success('已保存')
    fDlg.visible = false
    loadFewshot()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    fDlg.saving = false
  }
}

async function toggleEnabled(row, v) {
  try {
    await http.put(`/api/meta/fewshot/${row.id}`, {
      question: row.question, sql: row.sql,
      datasource_id: row.datasource_id || 0, enabled: v,
    })
    row.enabled = v
    ElMessage.success(v ? '已启用' : '已停用')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function removeFewshot(row) {
  try {
    await ElMessageBox.confirm(`删除样例「${row.question}」？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/meta/fewshot/${row.id}`)
    ElMessage.success('已删除')
    loadFewshot()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(() => {
  loadDs()
  loadFewshot()
})
</script>
