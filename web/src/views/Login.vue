<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-icon">D</div>
        <div>
          <div class="brand-name">DataPilot</div>
          <div class="brand-sub">AI-Native 大数据平台</div>
        </div>
      </div>
      <el-form :model="form" size="large" @keyup.enter="doLogin">
        <el-form-item>
          <el-input v-model="form.username" placeholder="用户名" :prefix-icon="User" autocomplete="username" />
        </el-form-item>
        <el-form-item>
          <el-input
            v-model="form.password"
            type="password"
            placeholder="密码"
            :prefix-icon="Lock"
            show-password
            autocomplete="current-password"
          />
        </el-form-item>
        <el-button type="primary" size="large" style="width: 100%" :loading="loading" @click="doLogin">
          登 录
        </el-button>
      </el-form>
      <el-divider><span class="muted">默认账号</span></el-divider>
      <div class="accounts">
        <span v-for="a in demoAccounts" :key="a.u" class="account-chip" @click="fill(a.u, a.p)">
          {{ a.u }} / {{ a.p }}
        </span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { User, Lock } from '@element-plus/icons-vue'
import http, { errMsg } from '../api'
import { useAuthStore } from '../store'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const demoAccounts = [
  { u: 'admin', p: 'admin123' },
  { u: 'engineer', p: 'engineer123' },
  { u: 'analyst', p: 'analyst123' },
  { u: 'viewer', p: 'viewer123' },
]

function fill(u, p) {
  form.username = u
  form.password = p
}

async function doLogin() {
  if (!form.username || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const res = await http.post('/api/auth/login', { username: form.username, password: form.password })
    auth.setLogin(res.token, res.user)
    ElMessage.success(`欢迎回来，${res.user.display_name || res.user.username}`)
    router.push(String(route.query.redirect || '/chat'))
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-wrap {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(1200px 500px at 20% -10%, rgba(37, 99, 235, 0.35), transparent 60%),
    radial-gradient(900px 500px at 90% 110%, rgba(124, 58, 237, 0.28), transparent 60%),
    #101828;
}
.login-card {
  width: 380px;
  background: #fff;
  border-radius: 14px;
  padding: 36px 34px 26px;
  box-shadow: 0 20px 50px rgba(2, 8, 20, 0.45);
}
.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}
.brand-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  background: linear-gradient(135deg, #2563eb, #7c3aed);
  color: #fff;
  font-size: 24px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.brand-name {
  font-size: 22px;
  font-weight: 700;
  color: #101828;
}
.brand-sub {
  font-size: 12px;
  color: #667085;
}
.accounts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.account-chip {
  font-size: 12px;
  color: #475467;
  background: #f2f4f7;
  border-radius: 20px;
  padding: 3px 10px;
  cursor: pointer;
}
.account-chip:hover {
  background: #e8effd;
  color: #2563eb;
}
</style>
