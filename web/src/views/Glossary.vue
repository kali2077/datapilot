<template>
  <div class="page-card">
    <div class="toolbar">
      <span class="muted">业务术语与同义词库 —— 智能问数 RAG 的一部分：问题命中术语族（术语/同义词/定义）时，
        整族注入上下文并按定义口径生成 SQL</span>
      <div class="spacer" />
      <el-button v-if="auth.canWrite" type="primary" icon="Plus" @click="openGlossary()">新建术语</el-button>
    </div>
    <el-table v-loading="glossaryLoading" :data="glossary" stripe>
      <el-table-column prop="term" label="术语" width="180">
        <template #default="{ row }"><el-tag effect="plain">{{ row.term }}</el-tag></template>
      </el-table-column>
      <el-table-column prop="synonyms" label="同义词" min-width="200">
        <template #default="{ row }">{{ row.synonyms || '-' }}</template>
      </el-table-column>
      <el-table-column prop="definition" label="定义（口径）" min-width="320" show-overflow-tooltip>
        <template #default="{ row }">{{ row.definition || '-' }}</template>
      </el-table-column>
      <el-table-column v-if="auth.canWrite" label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openGlossary(row)">编辑</el-button>
          <el-button link type="danger" @click="removeGlossary(row)">删除</el-button>
        </template>
      </el-table-column>
      <template #empty><el-empty description="暂无术语。例：GMV｜同义词：成交总额,流水｜定义：已支付订单的金额总和" /></template>
    </el-table>

    <el-dialog v-model="gDlg.visible" :title="gDlg.isEdit ? '编辑术语' : '新建术语'" width="480px" destroy-on-close>
      <el-form :model="gDlg.form" label-width="70px">
        <el-form-item label="术语" required><el-input v-model="gDlg.form.term" /></el-form-item>
        <el-form-item label="同义词"><el-input v-model="gDlg.form.synonyms" placeholder="逗号分隔，如: 销售额,营收" /></el-form-item>
        <el-form-item label="定义">
          <el-input v-model="gDlg.form.definition" type="textarea" :rows="3"
                    placeholder="业务口径，问数时 AI 按此生成 SQL，如：已支付订单的金额总和（status=paid 的 amount 求和）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="gDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="gDlg.saving" @click="saveGlossary">保存</el-button>
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
const glossary = ref([])
const glossaryLoading = ref(false)
const gDlg = reactive({ visible: false, isEdit: false, id: null, saving: false, form: { term: '', synonyms: '', definition: '' } })

async function loadGlossary() {
  glossaryLoading.value = true
  try {
    glossary.value = await http.get('/api/meta/glossary')
  } catch { /* ignore */ } finally {
    glossaryLoading.value = false
  }
}

function openGlossary(row) {
  gDlg.isEdit = !!row
  gDlg.id = row?.id || null
  gDlg.form = row
    ? { term: row.term, synonyms: row.synonyms || '', definition: row.definition || '' }
    : { term: '', synonyms: '', definition: '' }
  gDlg.visible = true
}

async function saveGlossary() {
  if (!gDlg.form.term.trim()) {
    ElMessage.warning('请填写术语')
    return
  }
  gDlg.saving = true
  try {
    if (gDlg.isEdit) await http.put(`/api/meta/glossary/${gDlg.id}`, gDlg.form)
    else await http.post('/api/meta/glossary', gDlg.form)
    ElMessage.success('已保存')
    gDlg.visible = false
    loadGlossary()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    gDlg.saving = false
  }
}

async function removeGlossary(row) {
  try {
    await ElMessageBox.confirm(`删除术语「${row.term}」？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/meta/glossary/${row.id}`)
    ElMessage.success('已删除')
    loadGlossary()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(loadGlossary)
</script>
