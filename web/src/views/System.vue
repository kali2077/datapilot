<template>
  <div class="page-card">
    <div class="toolbar">
      <span class="muted">共 {{ users.length }} 个用户</span>
      <div class="spacer" />
      <el-button icon="Refresh" circle @click="loadUsers" />
      <el-button type="primary" icon="Plus" @click="openUserDlg()">新建用户</el-button>
    </div>
    <el-table v-loading="userLoading" :data="users" stripe>
      <el-table-column prop="username" label="用户名" min-width="130">
        <template #default="{ row }"><span class="mono" style="font-weight: 600">{{ row.username }}</span></template>
      </el-table-column>
      <el-table-column prop="display_name" label="姓名" min-width="120" />
      <el-table-column label="角色" width="140">
        <template #default="{ row }">
          <el-select :model-value="row.role" size="small" @change="(v) => changeRole(row, v)">
            <el-option v-for="r in roles" :key="r.v" :label="r.l" :value="r.v" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-switch
            :model-value="row.status === 'active'" inline-prompt active-text="启用" inactive-text="停用"
            :disabled="row.id === auth.user?.id" @change="(v) => toggleUser(row, v)"
          />
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="170">
        <template #default="{ row }"><span class="muted">{{ row.created_at || '-' }}</span></template>
      </el-table-column>
      <el-table-column label="操作" width="110" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openUserDlg(row)">编辑</el-button>
        </template>
      </el-table-column>
      <template #empty><el-empty description="暂无用户" /></template>
    </el-table>

    <!-- 用户新建/编辑 -->
    <el-dialog v-model="userDlg.visible" :title="userDlg.isEdit ? `编辑用户：${userDlg.row?.username || ''}` : '新建用户'" width="460px" destroy-on-close>
      <el-form :model="userDlg.form" label-width="80px">
        <el-form-item v-if="!userDlg.isEdit" label="用户名" required>
          <el-input v-model="userDlg.form.username" />
        </el-form-item>
        <el-form-item label="姓名">
          <el-input v-model="userDlg.form.display_name" placeholder="显示名称" />
        </el-form-item>
        <el-form-item :label="userDlg.isEdit ? '重置密码' : '密码'" :required="!userDlg.isEdit">
          <el-input
            v-model="userDlg.form.password" type="password" show-password
            :placeholder="userDlg.isEdit ? '留空则不修改' : '至少 6 位'"
          />
        </el-form-item>
        <el-form-item label="角色" required>
          <el-select v-model="userDlg.form.role" style="width: 100%">
            <el-option v-for="r in roles" :key="r.v" :label="r.l" :value="r.v" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="userDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="userDlg.saving" @click="saveUser">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import { useAuthStore, ROLE_LABEL } from '../store'

const auth = useAuthStore()
const roles = Object.entries(ROLE_LABEL).map(([v, l]) => ({ v, l }))

const users = ref([])
const userLoading = ref(false)
const userDlg = reactive({
  visible: false, isEdit: false, row: null, saving: false,
  form: { username: '', display_name: '', password: '', role: 'viewer' },
})

async function loadUsers() {
  userLoading.value = true
  try {
    users.value = await http.get('/api/auth/users')
  } catch { /* ignore */ } finally {
    userLoading.value = false
  }
}

function openUserDlg(row) {
  userDlg.isEdit = !!row
  userDlg.row = row || null
  userDlg.form = {
    username: row?.username || '',
    display_name: row?.display_name || '',
    password: '',
    role: row?.role || 'viewer',
  }
  userDlg.visible = true
}

async function saveUser() {
  const f = userDlg.form
  if (!userDlg.isEdit) {
    if (!f.username.trim() || f.password.length < 6) {
      ElMessage.warning('请填写用户名，密码至少 6 位')
      return
    }
    userDlg.saving = true
    try {
      await http.post('/api/auth/users', {
        username: f.username.trim(), password: f.password,
        display_name: f.display_name || f.username.trim(), role: f.role,
      })
      ElMessage.success('用户已创建')
      userDlg.visible = false
      loadUsers()
    } catch (e) {
      ElMessage.error(errMsg(e))
    } finally {
      userDlg.saving = false
    }
  } else {
    const payload = {}
    if (f.display_name !== userDlg.row.display_name) payload.display_name = f.display_name
    if (f.role !== userDlg.row.role) payload.role = f.role
    if (f.password) payload.password = f.password
    if (!Object.keys(payload).length) {
      ElMessage.info('无变更')
      userDlg.visible = false
      return
    }
    userDlg.saving = true
    try {
      await http.put(`/api/auth/users/${userDlg.row.id}`, payload)
      ElMessage.success('已保存')
      userDlg.visible = false
      loadUsers()
    } catch (e) {
      ElMessage.error(errMsg(e))
    } finally {
      userDlg.saving = false
    }
  }
}

async function changeRole(row, role) {
  try {
    await http.put(`/api/auth/users/${row.id}`, { role })
    row.role = role
    ElMessage.success('角色已更新')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function toggleUser(row, active) {
  const status = active ? 'active' : 'disabled'
  try {
    await ElMessageBox.confirm(
      `${active ? '启用' : '停用'}用户「${row.username}」？`, '确认', { type: 'warning' }
    )
  } catch { return }
  try {
    await http.put(`/api/auth/users/${row.id}`, { status })
    row.status = status
    ElMessage.success('已更新')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(() => {
  loadUsers()
})
</script>
