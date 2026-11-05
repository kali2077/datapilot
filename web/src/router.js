import { createRouter, createWebHashHistory } from 'vue-router'
import { useAuthStore } from './store'

const Layout = () => import('./layout/MainLayout.vue')

const ALL = ['admin', 'engineer', 'analyst', 'viewer']

export const routes = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('./views/Login.vue'),
    meta: { public: true, title: '登录' },
  },
  {
    path: '/',
    component: Layout,
    redirect: '/chat',
    children: [
      { path: 'datasources', name: 'Datasources', component: () => import('./views/Datasources.vue'), meta: { title: '数据源管理', roles: ['admin', 'engineer'] } },
      { path: 'metadata', name: 'Metadata', component: () => import('./views/Metadata.vue'), meta: { title: '元数据管理', roles: ['admin', 'engineer'] } },
      { path: 'query', name: 'Query', component: () => import('./views/Query.vue'), meta: { title: '数据查询', roles: ALL } },
      { path: 'assets', name: 'Assets', component: () => import('./views/Assets.vue'), meta: { title: '数据资产地图', roles: ALL } },
      { path: 'assets/:id', name: 'AssetDetail', component: () => import('./views/AssetDetail.vue'), meta: { title: '表详情', tabKey: '/assets-detail', roles: ALL } },
      { path: 'integration', name: 'Integration', component: () => import('./views/Integration.vue'), meta: { title: '数据集成', roles: ['admin', 'engineer'] } },
      { path: 'workflows', name: 'Workflows', component: () => import('./views/Workflows.vue'), meta: { title: '工作流开发', roles: ['admin', 'engineer'] } },
      { path: 'ops', name: 'Ops', component: () => import('./views/Ops.vue'), meta: { title: '调度运维', roles: ['admin', 'engineer'] } },
      { path: 'monitoring', name: 'Monitoring', component: () => import('./views/Monitoring.vue'), meta: { title: '数据监控告警', roles: ['admin', 'engineer'] } },
      { path: 'chat', name: 'Chat', component: () => import('./views/Chat.vue'), meta: { title: '问数对话', tabKey: '/chat', roles: ALL } },
      { path: 'fewshots', name: 'FewShot', component: () => import('./views/FewShot.vue'), meta: { title: 'SQL 示例', roles: ALL } },
      { path: 'glossary', name: 'Glossary', component: () => import('./views/Glossary.vue'), meta: { title: '术语库', roles: ALL } },
      { path: 'ai-assistant', name: 'AiAssistant', component: () => import('./views/AiAssistant.vue'), meta: { title: 'AI 助手', roles: ALL } },
      { path: 'apis', name: 'Apis', component: () => import('./views/Apis.vue'), meta: { title: '数据服务', roles: ['admin', 'engineer', 'analyst'] } },
      { path: 'audit', name: 'Audit', component: () => import('./views/Audit.vue'), meta: { title: '审计日志', roles: ['admin'] } },
      { path: 'users', name: 'Users', component: () => import('./views/System.vue'), meta: { title: '用户管理', roles: ['admin'] } },
    ],
  },
  { path: '/:pathMatch(.*)*', redirect: '/chat' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  const auth = useAuthStore()
  if (to.meta.public) {
    // 已登录访问登录页 → 回首页
    if (auth.token && to.path === '/login') return '/chat'
    return true
  }
  if (!auth.token) {
    return { path: '/login', query: to.fullPath && to.fullPath !== '/' ? { redirect: to.fullPath } : {} }
  }
  if (to.meta.roles && !to.meta.roles.includes(auth.role)) {
    return '/chat'
  }
  return true
})

export default router
