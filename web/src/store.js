import { defineStore } from 'pinia'

export const ROLE_LABEL = {
  admin: '管理员',
  engineer: '数据工程师',
  analyst: '数据分析师',
  viewer: '观察者',
}

export const ROLE_TAG = {
  admin: 'danger',
  engineer: 'warning',
  analyst: 'primary',
  viewer: 'info',
}

/**
 * 侧边栏菜单配置（按角色过滤）
 * admin: 全部；engineer: 除审计外全部；analyst: 查询/问数/资产地图/数据服务；viewer: 查询/问数/资产地图
 */
export const MENU = [
  {
    title: '数据管理',
    items: [
      { path: '/datasources', label: '数据源管理', icon: 'Coin', roles: ['admin', 'engineer'] },
      { path: '/metadata', label: '元数据管理', icon: 'Collection', roles: ['admin', 'engineer'] },
      { path: '/query', label: '数据查询', icon: 'Search', roles: ['admin', 'engineer', 'analyst', 'viewer'] },
      { path: '/assets', label: '数据资产地图', icon: 'MapLocation', roles: ['admin', 'engineer', 'analyst', 'viewer'] },
    ],
  },
  {
    title: '数据开发',
    items: [
      { path: '/integration', label: '数据集成', icon: 'Connection', roles: ['admin', 'engineer'] },
      { path: '/workflows', label: '工作流开发', icon: 'SetUp', roles: ['admin', 'engineer'] },
      { path: '/ops', label: '调度运维', icon: 'Monitor', roles: ['admin', 'engineer'] },
      { path: '/monitoring', label: '数据监控告警', icon: 'Bell', roles: ['admin', 'engineer'] },
    ],
  },
  {
    title: '智能应用',
    items: [
      { path: '/ai-assistant', label: 'AI 助手', icon: 'MagicStick', roles: ['admin', 'engineer', 'analyst', 'viewer'] },
      { path: '/chat', label: '智能问数', icon: 'ChatDotRound', roles: ['admin', 'engineer', 'analyst', 'viewer'],
        children: [
          { path: '/chat', label: '问数对话' },
          { path: '/fewshots', label: 'SQL 示例' },
          { path: '/glossary', label: '术语库' },
        ] },
      { path: '/apis', label: '数据服务', icon: 'Link', roles: ['admin', 'engineer', 'analyst'] },
    ],
  },
  {
    title: '系统管理',
    items: [
      { path: '/audit', label: '审计日志', icon: 'Document', roles: ['admin'] },
      { path: '/users', label: '用户管理', icon: 'User', roles: ['admin'] },
    ],
  },
]

/** 多标签页导航：每个路由一个标签，点击切换、可关闭（首页固定）。 */
export const useTabsStore = defineStore('tabs', {
  state: () => ({
    tabs: [],            // [{ path, title, affix }]
    active: '/chat',
  }),
  actions: {
    addTab(path, title, affix = false) {
      if (!this.tabs.find((t) => t.path === path)) {
        this.tabs.push({ path, title, affix })
      }
      this.active = path
    },
    removeTab(path) {
      const idx = this.tabs.findIndex((t) => t.path === path)
      if (idx < 0) return null
      const removed = this.tabs.splice(idx, 1)[0]
      if (removed.affix) {           // 固定标签只关内容，标签保留
        this.tabs.unshift({ path: removed.path, title: removed.title, affix: true })
        return null
      }
      if (this.active === path) {
        const next = this.tabs[idx] || this.tabs[idx - 1]
        this.active = next ? next.path : '/chat'
        return this.active           // 调用方负责跳转
      }
      return null
    },
  },
})

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('dp_token') || '',
    user: JSON.parse(localStorage.getItem('dp_user') || 'null'),
  }),
  getters: {
    role: (s) => s.user?.role || 'viewer',
    displayName: (s) => s.user?.display_name || s.user?.username || '未知用户',
    isAdmin: (s) => (s.user?.role || '') === 'admin',
    isEngineer: (s) => ['admin', 'engineer'].includes(s.user?.role),
    canWrite: (s) => ['admin', 'engineer'].includes(s.user?.role),
    /** 当前角色可见的菜单 */
    menus: (s) => MENU.map((g) => ({
      title: g.title,
      items: g.items
        .filter((it) => it.roles.includes(s.user?.role))
        .map((it) => ({ ...it, label: s.user?.role === 'analyst' && it.analystLabel ? it.analystLabel : it.label })),
    })).filter((g) => g.items.length > 0),
  },
  actions: {
    setLogin(token, user) {
      this.token = token
      this.user = user
      localStorage.setItem('dp_token', token)
      localStorage.setItem('dp_user', JSON.stringify(user))
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('dp_token')
      localStorage.removeItem('dp_user')
    },
  },
})
