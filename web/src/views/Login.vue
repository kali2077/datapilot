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

      <form class="login-form" @submit.prevent="doLogin">
        <label class="field">
          <span class="field-label">用户名</span>
          <el-input v-model="form.username" placeholder="输入用户名" :prefix-icon="User"
                    autocomplete="username" autofocus />
        </label>
        <label class="field">
          <span class="field-label">密码</span>
          <el-input v-model="form.password" type="password" placeholder="输入密码" :prefix-icon="Lock"
                    show-password autocomplete="current-password" />
        </label>
        <p v-if="loginError" class="form-error" role="alert">{{ loginError }}</p>
        <el-button native-type="submit" type="primary" size="large" class="submit" :loading="loading">
          登录
        </el-button>
      </form>

      <el-divider><span class="muted">演示账号 · 点击填充</span></el-divider>
      <div class="accounts">
        <button v-for="a in demoAccounts" :key="a.u" type="button" class="account-chip" @click="fill(a.u, a.p)">
          {{ a.u }} / {{ a.p }}
        </button>
      </div>

      <p class="trust-line">内部部署平台 · 请勿使用生产凭据测试</p>
    </div>
  </div>
</template>

<script setup>
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { User, Lock } from '@element-plus/icons-vue'
import http, { errMsg } from '../api'
import { useAuthStore } from '../store'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const form = reactive({ username: '', password: '' })
const loading = ref(false)
const loginError = ref('')
const demoAccounts = [
  { u: 'admin', p: 'admin123' },
  { u: 'engineer', p: 'engineer123' },
  { u: 'analyst', p: 'analyst123' },
  { u: 'viewer', p: 'viewer123' },
]

function fill(u, p) {
  form.username = u
  form.password = p
  loginError.value = ''
}

async function doLogin() {
  loginError.value = ''
  if (!form.username || !form.password) {
    loginError.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  try {
    const res = await http.post('/api/auth/login', { username: form.username, password: form.password })
    auth.setLogin(res.token, res.user)
    router.push(String(route.query.redirect || '/chat'))
  } catch (e) {
    loginError.value = errMsg(e) || '登录失败,请检查用户名或密码'
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
  background: var(--dp-bg);
  padding: 24px;
}
.login-card {
  width: 380px;
  background: var(--dp-surface);
  border: 1px solid var(--dp-border);
  border-radius: var(--dp-radius-lg);
  padding: 32px 30px 22px;
  box-shadow: var(--dp-shadow-pop);
}
.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 26px;
}
.brand-icon {
  width: 42px;
  height: 42px;
  border-radius: 10px;
  background: linear-gradient(135deg, var(--dp-accent), var(--dp-brand-2));
  color: #fff;
  font-size: 22px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
}
.brand-name {
  font-size: 21px;
  font-weight: 700;
  letter-spacing: -0.02em;
  color: var(--dp-text-1);
}
.brand-sub {
  font-size: 12px;
  color: var(--dp-text-3);
}
.login-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--dp-text-2);
}
.form-error {
  margin: 0;
  font-size: 12.5px;
  color: var(--dp-danger);
}
.submit {
  width: 100%;
  margin-top: 2px;
}
.accounts {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.account-chip {
  font-family: var(--dp-mono);
  font-size: 12px;
  color: var(--dp-text-2);
  background: var(--dp-bg-subtle);
  border: 1px solid var(--dp-border-soft);
  border-radius: var(--dp-radius-sm);
  padding: 3px 10px;
  cursor: pointer;
  transition: color 0.1s, border-color 0.1s;
}
.account-chip:hover {
  color: var(--dp-accent);
  border-color: var(--dp-accent);
}
.trust-line {
  margin: 18px 0 0;
  text-align: center;
  font-size: 11px;
  color: var(--dp-text-3);
}
</style>
