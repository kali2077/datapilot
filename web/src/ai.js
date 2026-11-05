/** 指令示范：按页面上下文推荐可点击的示例指令（全部可被规则引擎/LLM 理解） */
export function openAi(prefill = '') {
  const q = prefill ? `?prefill=${encodeURIComponent(prefill)}` : ''
  location.hash = `#/ai-assistant${q}`
}

/** 指令示范：按页面上下文推荐可点击的示例指令（全部可被规则引擎/LLM 理解） */
export const PAGE_SUGGESTIONS = {
  '/integration': [
    '把 demo_source 的 orders 表每天凌晨2点同步到 demo_warehouse 的 ods_orders',
    '找订单相关的表',
    '查 demo_source 的 orders 有多少行',
  ],
  '/workflows': [
    '基于 ods_orders 做 dwd 订单明细，剔除测试账号，每天凌晨3点跑',
    '看看监控情况',
  ],
  '/ops': [
    '诊断实例 1',
    '暂停 sync_orders_to_ods_orders',
    '恢复 sync_orders_to_ods_orders',
  ],
  '/monitoring': [
    '看看监控情况',
    '立即检查 ods_orders 行数波动监控',
  ],
  '/assets': [
    '找订单相关的表',
    '介绍一下表 orders',
  ],
  '/query': [
    '统计每天订单量',
    '查 demo_source 的 customers 前5行',
  ],
  '/apis': [
    '查 demo_source 的 orders 前5行',
    '把上一条查询发布成API',
  ],
  '/chat': [
    '统计每天订单量',
    '找订单相关的表',
    '看看监控情况',
    '你能做什么',
  ],
  '/ai-assistant': [
    '把 demo_source 的 orders 表每天凌晨2点同步到 demo_warehouse 的 ods_orders',
    '基于 ods_orders 做 dwd 订单明细，剔除测试账号，每天凌晨3点跑',
    '统计每天订单量',
    '看看监控情况',
    '你能做什么',
  ],
  '/datasources': [
    '找订单相关的表',
    '看看监控情况',
  ],
}

export const DEFAULT_SUGGESTIONS = [
  '查 demo_source 的 orders 前10行',
  '找订单相关的表',
  '看看监控情况',
  '你能做什么',
]

export function suggestionsFor(path) {
  return PAGE_SUGGESTIONS[path] || DEFAULT_SUGGESTIONS
}
