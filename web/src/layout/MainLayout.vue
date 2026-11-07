<template>
  <el-container class="layout-root" style="height: 100vh">
    <el-aside class="app-sidebar" width="220px">
      <div class="logo">
        <div class="logo-icon">D</div>
        <div>
          <div class="logo-text">DataPilot</div>
          <div class="logo-sub">AI-Native 数据平台</div>
        </div>
      </div>
      <el-menu ref="menuRef" :default-active="route.path" router unique-opened>
        <el-menu-item v-if="biUrl" index="/bi" @click="openBi()">
          <el-icon><DataAnalysis /></el-icon>
          <span>BI 分析 (Superset)</span>
        </el-menu-item>
        <template v-for="group in auth.menus" :key="group.title">
          <el-menu-item-group :title="group.title">
            <template v-for="item in group.items" :key="item.path">
              <el-sub-menu v-if="item.children?.length" :index="item.path">
                <template #title>
                  <el-icon><component :is="item.icon" /></el-icon>
                  <span>{{ item.label }}</span>
                </template>
                <el-menu-item v-for="ch in item.children" :key="ch.path" :index="ch.path">
                  {{ ch.label }}
                </el-menu-item>
              </el-sub-menu>
              <el-menu-item v-else :index="item.path">
                <el-icon><component :is="item.icon" /></el-icon>
                <span>{{ item.label }}</span>
              </el-menu-item>
            </template>
          </el-menu-item-group>
        </template>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="layout-header" height="56px">
        <div class="header-title">{{ pageTitle }}</div>
        <div class="header-actions">
          <el-tooltip :content="isDark ? '切换到浅色模式' : '切换到暗色模式'" placement="bottom">
            <el-button text :icon="isDark ? Sunny : Moon" aria-label="切换主题" @click="toggleTheme" />
          </el-tooltip>
          <el-dropdown trigger="click">
            <span class="user-entry">
              <el-avatar :size="26" class="user-avatar">{{ userInitial }}</el-avatar>
              <span class="user-name">{{ auth.displayName }}</span>
              <el-tag :type="roleTag" size="small" effect="light">{{ roleLabel }}</el-tag>
              <el-icon class="user-caret"><ArrowDown /></el-icon>
            </span>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  <el-icon><UserFilled /></el-icon>{{ roleLabel }} · {{ auth.user?.username || auth.displayName }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="onLogout">
                  <el-icon><SwitchButton /></el-icon>退出登录
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </el-header>

      <!-- 多标签页导航（支持中键关闭标签） -->
      <div class="tabs-bar" @auxclick="onTabAuxClick">
        <el-tabs
          v-model="tabs.active" type="card" class="layout-tabs"
          @tab-click="onTabClick" @tab-remove="onTabRemove"
        >
          <el-tab-pane
            v-for="t in tabs.tabs" :key="t.path"
            :label="t.title" :name="t.path" :closable="!t.affix"
          />
        </el-tabs>
      </div>

      <el-main class="layout-main">
        <router-view />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Sunny, Moon } from '@element-plus/icons-vue'
import { useAuthStore, useTabsStore, ROLE_LABEL, ROLE_TAG } from '../store'
import { useTheme } from '../composables/theme'
import http from '../api'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const tabs = useTabsStore()
const menuRef = ref()
const { isDark, toggle: toggleTheme } = useTheme()

// 中键关闭标签（浏览器习惯）：el-tabs 不透出中键事件，监听原生 auxclick
function onTabAuxClick(e) {
  if (e.button !== 1) return
  const item = e.target?.closest?.('.el-tabs__item')
  if (!item) return
  e.preventDefault()
  const name = (item.id || '').replace(/^tab-/, '')
  if (name) onTabRemove(name)
}

// 当前路由属于某子导航时，自动展开其父菜单
watch(() => route.path, (p) => {
  for (const g of auth.menus) {
    for (const it of g.items) {
      if (it.children?.some((c) => c.path === p)) {
        menuRef.value?.open(it.path)
        return
      }
    }
  }
}, { immediate: true })

const biUrl = ref('')
onMounted(async () => {
  try { biUrl.value = (await http.get('/api/settings')).superset_url || '' } catch { /* ignore */ }
})
function openBi() { window.open(biUrl.value, '_blank') }

const roleLabel = computed(() => ROLE_LABEL[auth.role] || auth.role)
const roleTag = computed(() => ROLE_TAG[auth.role] || 'info')
const pageTitle = computed(() => route.meta.title || 'DataPilot')
const userInitial = computed(() => (auth.displayName || 'U').slice(0, 1))

// 路由变化 -> 打开/激活对应标签 + 同步浏览器标签页标题
watch(() => route.path, (p) => {
  document.title = route.meta.public
    ? 'DataPilot 数据平台'
    : `${route.meta.title || 'DataPilot'} · DataPilot`
  if (route.meta.public) return
  const key = route.meta.tabKey || p
  const title = route.meta.title || '页面'
  tabs.addTab(key, title, key === '/chat')
  tabs.active = key
}, { immediate: true })

function onTabClick({ paneName }) {
  if (paneName && paneName !== route.path) router.push(paneName)
}

function onTabRemove(name) {
  const next = tabs.removeTab(String(name))
  if (next) router.push(next)
}

function onLogout() {
  auth.logout()
  router.push('/login')
}
</script>

<style scoped>
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.user-entry {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  padding: 4px 6px;
  border-radius: 6px;
  transition: background-color 0.1s;
  outline: none;
}
.user-entry:hover {
  background: var(--dp-accent-soft);
}
.user-avatar {
  background: var(--dp-accent-soft);
  color: var(--dp-accent);
  font-size: 13px;
  font-weight: 600;
  flex: none;
}
.user-name {
  font-weight: 600;
  color: var(--dp-text-1);
  font-size: 13px;
}
.user-caret {
  color: var(--dp-text-3);
  font-size: 12px;
}
</style>
