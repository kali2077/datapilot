<template>
  <!-- 表格 + 图表 + SQL -->
  <div v-if="card.type === 'table'">
    <el-table :data="tableRows" size="small" border max-height="340" stripe>
      <el-table-column
        v-for="(c, i) in card.columns || []" :key="i" :label="String(c)" :prop="'c' + i"
        min-width="110" show-overflow-tooltip
      />
    </el-table>
    <div style="display: flex; align-items: center; gap: 8px; margin-top: 6px">
      <span class="muted">{{ (card.rows || []).length }} 行</span>
      <el-tag v-if="card.datasource" size="small" effect="plain" type="info">数据源: {{ card.datasource }}</el-tag>
    </div>
    <div v-if="card.chart && (card.chart.y || []).length" style="margin-top: 12px">
      <BarChart :card="card" />
    </div>
    <el-collapse v-if="card.sql" style="margin-top: 10px">
      <el-collapse-item title="查看 SQL" name="sql">
        <pre class="sql-pre">{{ card.sql }}</pre>
      </el-collapse-item>
    </el-collapse>
  </div>

  <!-- 表清单 -->
  <div v-else-if="card.type === 'tables'" class="tables-card">
    <div v-for="(t, i) in card.tables || []" :key="i" class="table-item">
      <el-icon color="#2563eb"><Grid /></el-icon>
      <span class="mono" style="font-weight: 600">{{ t.table }}</span>
      <span class="muted">{{ t.comment || '' }}</span>
      <el-tag v-if="t.rows !== undefined" size="small" type="info">{{ t.rows }} 行</el-tag>
    </div>
  </div>

  <!-- 表结构 -->
  <div v-else-if="card.type === 'schema'">
    <div class="muted" style="margin-bottom: 8px">
      表 <b class="mono">{{ card.table }}</b>
      <el-tag v-if="card.datasource" size="small" type="info" style="margin-left: 6px">{{ card.datasource }}</el-tag>
    </div>
    <el-table :data="card.columns || []" size="small" border max-height="320">
      <el-table-column prop="name" label="字段" min-width="120">
        <template #default="{ row }"><span class="mono">{{ row.name }}</span></template>
      </el-table-column>
      <el-table-column prop="type" label="类型" width="110">
        <template #default="{ row }"><span class="mono muted">{{ row.type }}</span></template>
      </el-table-column>
      <el-table-column prop="comment" label="注释" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">{{ row.comment || '-' }}</template>
      </el-table-column>
    </el-table>
  </div>

  <!-- 确认执行 -->
  <div v-else-if="card.type === 'confirm'" class="confirm-card">
    <div class="cc-head">
      <el-icon><InfoFilled /></el-icon>
      <span>{{ card.title || '请确认以下操作' }}</span>
    </div>
    <el-descriptions v-if="displayPairs.length" :column="1" size="small" border>
      <el-descriptions-item v-for="p in displayPairs" :key="p.k" :label="p.k">{{ p.v }}</el-descriptions-item>
    </el-descriptions>

    <template v-if="card.trial">
      <div class="cc-sub">试跑统计（100 行）</div>
      <el-descriptions :column="4" size="small" border>
        <el-descriptions-item label="读取">{{ card.trial.stats?.read_rows ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="写入">{{ card.trial.stats?.written_rows ?? '-' }}</el-descriptions-item>
        <el-descriptions-item label="耗时">{{ card.trial.stats?.duration_ms ?? '-' }} ms</el-descriptions-item>
        <el-descriptions-item label="建表">{{ card.trial.stats?.auto_created ? '自动' : '已存在' }}</el-descriptions-item>
      </el-descriptions>
      <div v-if="(card.trial.rows || []).length" class="cc-sub">样例数据（前 5 行）</div>
      <el-table v-if="(card.trial.rows || []).length" :data="trialPreview" size="small" border max-height="180">
        <el-table-column
          v-for="(c, i) in card.trial.columns || []" :key="i" :label="String(c)" :prop="'c' + i"
          min-width="110" show-overflow-tooltip
        />
      </el-table>
    </template>

    <template v-if="(card.issues || []).length">
      <div class="cc-sub">校验问题</div>
      <el-alert
        v-for="(is, i) in card.issues" :key="i" :closable="false" show-icon style="margin-bottom: 6px"
        :type="is.level === 'error' ? 'error' : is.level === 'warning' ? 'warning' : 'info'"
        :title="is.message"
      />
    </template>
    <template v-if="card.ddl">
      <div class="cc-sub">建表 DDL</div>
      <pre class="sql-pre">{{ card.ddl }}</pre>
    </template>
    <template v-if="card.sql">
      <div class="cc-sub">SQL</div>
      <pre class="sql-pre">{{ card.sql }}</pre>
    </template>
    <template v-if="card.plan && card.plan.length">
      <div class="cc-sub">执行计划</div>
      <pre class="sql-pre">{{ card.plan.join('\n') }}</pre>
    </template>

    <div v-if="pending" class="cc-actions">
      <el-button type="primary" icon="CircleCheck" :loading="acting" @click="$emit('confirm')">确认执行</el-button>
      <el-button icon="CircleClose" :disabled="acting" @click="$emit('cancel')">取消</el-button>
    </div>
    <div v-else style="margin-top: 12px">
      <el-tag v-if="confirmStatus === 'confirmed'" type="success" effect="dark" size="small">已确认执行</el-tag>
      <el-tag v-else-if="confirmStatus === 'expired'" type="info" size="small">已取消 / 已过期</el-tag>
    </div>
  </div>

  <!-- 校验问题列表 -->
  <div v-else-if="card.type === 'issues'">
    <el-alert
      v-for="(is, i) in card.issues || []" :key="i" :closable="false" show-icon style="margin-bottom: 6px"
      :type="is.level === 'error' ? 'error' : is.level === 'warning' ? 'warning' : 'info'"
      :title="is.message"
    />
  </div>

  <!-- 链接 -->
  <div v-else-if="card.type === 'job_link'">
    <el-button type="primary" plain icon="SetUp" @click="$router.push('/workflows')">
      前往工作流{{ card.workflow_id ? ` #${card.workflow_id}` : '' }}
    </el-button>
  </div>
  <div v-else-if="card.type === 'instance_link'">
    <el-button type="primary" plain icon="Monitor" @click="$router.push('/ops')">
      查看运行实例{{ card.instance_id ? ` #${card.instance_id}` : '' }}
    </el-button>
  </div>
  <div v-else-if="card.type === 'api_link'">
    <el-button type="primary" plain icon="Link" @click="$router.push('/apis')">
      查看 API{{ card.code ? ` ${card.code}` : '' }}
    </el-button>
  </div>

  <!-- 纯文本卡片：内容已在气泡中，不额外渲染 -->
  <div v-else-if="card.type === 'text'" />

  <!-- 未知/兜底 -->
  <div v-else-if="card && Object.keys(card).length">
    <pre class="sql-pre">{{ JSON.stringify(card, null, 2) }}</pre>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import BarChart from './BarChart.vue'

const props = defineProps({
  msg: { type: Object, required: true },
})
defineEmits(['confirm', 'cancel'])

const card = computed(() => props.msg.card || {})

const tableRows = computed(() =>
  (card.value.rows || []).map((r) => Object.fromEntries((r || []).map((v, i) => [`c${i}`, v])))
)

const trialPreview = computed(() =>
  ((card.value.trial?.rows || []).slice(0, 5)).map((r) => Object.fromEntries((r || []).map((v, i) => [`c${i}`, v])))
)

const displayPairs = computed(() =>
  Object.entries(card.value.display || {}).map(([k, v]) => ({ k, v: String(v) }))
)

const pending = computed(() => props.msg.confirm_status === 'pending' && !!props.msg.confirm_token)
const confirmStatus = computed(() => props.msg.confirm_status)
const acting = computed(() => !!props.msg._acting)
</script>

<style scoped>
.tables-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.table-item {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #e4e7ec;
  border-radius: 6px;
  padding: 7px 10px;
  background: #fafbfc;
}
</style>
