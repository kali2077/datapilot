<template>
  <div class="chat-layout">
    <!-- 会话侧栏 -->
    <div class="chat-side">
      <div class="side-head">
        <el-button type="primary" icon="Plus" style="width: 100%" :loading="creating" @click="newSession">新建会话</el-button>
      </div>
      <div class="session-list" v-loading="sessLoading">
        <div
          v-for="s in sessions" :key="s.id" class="session-item"
          :class="{ active: currentId === s.id }" @click="selectSession(s.id)"
        >
          <el-icon><ChatDotRound /></el-icon>
          <span class="title">{{ s.title || `会话 #${s.id}` }}</span>
          <el-icon class="del" title="删除会话" @click.stop="removeSession(s)"><Delete /></el-icon>
        </div>
        <el-empty v-if="!sessions.length && !sessLoading" description="暂无会话" :image-size="70" />
      </div>
    </div>

    <!-- 消息区 -->
    <div class="chat-main">
      <div ref="msgBox" class="chat-messages">
        <template v-if="currentId">
          <div v-if="!messages.length && !msgLoading" style="height: 100%; display: flex; align-items: center; justify-content: center">
            <el-empty description="AI 全能力助手：建同步/ETL 任务 · 查数 · 调度运维 · 监控 · 发布 API">
              <template #image><el-icon :size="60" color="var(--dp-accent)"><ChatDotRound /></el-icon></template>
            </el-empty>
          </div>

          <div v-for="m in messages" :key="m.id" class="chat-msg-row" :class="m.role">
            <template v-if="m.role === 'user'">
              <div class="chat-bubble-user">
                <div v-if="m.attachments?.length" class="msg-atts">
                  <template v-for="a in m.attachments" :key="a.id">
                    <el-image v-if="a.type === 'image'" class="msg-att-img" :src="attUrl(a)"
                              :preview-src-list="[attUrl(a)]" preview-teleported hide-on-click-modal fit="cover" />
                    <a v-else class="msg-att-file" :href="attUrl(a)" target="_blank">
                      <el-icon><component :is="attIcon(a.type)" /></el-icon>{{ a.name }}
                    </a>
                  </template>
                </div>{{ m.content }}
              </div>
            </template>
            <template v-else>
              <div class="chat-bubble-ai">
                <div class="ai-card">
                  <div class="ai-content">{{ m.content }}</div>
                  <ChatCard v-if="m.card && m.card.type" :msg="m" @confirm="doConfirm(m)" @cancel="doCancel(m)" />
                  <div class="ai-meta" v-if="m.provider || m.ts || m.content">
                    <el-tooltip content="复制回复" placement="top">
                      <el-icon class="copy-btn" @click="copyMsg(m)"><DocumentCopy /></el-icon>
                    </el-tooltip>
                    <el-tag v-if="providerLabel(m.provider)" size="small" type="info" effect="plain">
                      {{ providerLabel(m.provider) }}
                    </el-tag>
                    <span class="muted">{{ m.ts }}</span>
                  </div>
                </div>
              </div>
            </template>
          </div>

          <div v-if="sending" class="chat-msg-row assistant">
            <div class="chat-bubble-ai">
              <div class="ai-card" style="display: flex; align-items: center; gap: 10px; color: var(--dp-text-3)">
                <el-icon class="is-loading"><Loading /></el-icon> 正在思考与查询…
              </div>
            </div>
          </div>
        </template>
        <el-empty v-else description="选择或新建一个会话" />
      </div>

      <ChatInput ref="inputRef" :session-id="currentId" :sending="sending" :disabled="!currentId"
                 placeholder="向 AI 助手下指令，Enter 发送（创建类操作需在对话中确认）"
                 hint="AI 助手横切全部模块：数据集成 / 数据开发 / 调度运维 / 监控 / 数据服务（高危操作需确认）"
                 @send="onSend">
        <template #top>
          <PromptChips page="/ai-assistant" @pick="inputRef?.setText($event)" />
        </template>
      </ChatInput>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DocumentCopy } from '@element-plus/icons-vue'
import http, { errMsg } from '../api'
import { copyText } from '../utils/clipboard'
import { useAuthStore } from '../store'
import ChatCard from '../components/ChatCard.vue'
import ChatInput from '../components/ChatInput.vue'
import PromptChips from '../components/PromptChips.vue'

const auth = useAuthStore()
const route = useRoute()
const sessions = ref([])
const sessLoading = ref(false)
const creating = ref(false)
const currentId = ref(null)
const messages = ref([])
const msgLoading = ref(false)
const inputRef = ref(null)
const sending = ref(false)
const msgBox = ref(null)

let localSeq = -1

// 附件 URL 带 token（img/label 无法携带 Authorization 头）
function attUrl(a) {
  return `${a.url}?token=${auth.token}`
}

function attIcon(t) {
  return { table: 'Grid', doc: 'Reading' }[t] || 'Document'
}

async function copyMsg(m) {
  const ok = await copyText(m.content || '')
  ok ? ElMessage.success('已复制') : ElMessage.error('复制失败，请手动选择文本')
}

function providerLabel(p) {
  if (!p) return ''
  return { 'rule-engine': '离线规则引擎', confirm: '执行确认', llm: '大模型', agent: 'Agent' }[p] || p
}

function scrollToBottom() {
  nextTick(() => {
    if (msgBox.value) msgBox.value.scrollTop = msgBox.value.scrollHeight
  })
}

async function loadSessions() {
  sessLoading.value = true
  try {
    sessions.value = await http.get('/api/chat/sessions')
  } catch { /* ignore */ } finally {
    sessLoading.value = false
  }
}

async function newSession() {
  creating.value = true
  try {
    const res = await http.post('/api/chat/sessions')
    sessions.value.unshift({ id: res.id, title: res.title || '新对话' })
    selectSession(res.id)
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    creating.value = false
  }
}

async function selectSession(id) {
  currentId.value = id
  messages.value = []
  msgLoading.value = true
  try {
    messages.value = await http.get(`/api/chat/sessions/${id}/messages`)
    scrollToBottom()
  } catch { /* ignore */ } finally {
    msgLoading.value = false
  }
}

async function removeSession(s) {
  try {
    await ElMessageBox.confirm(`删除会话「${s.title || `#${s.id}`}」及其全部消息？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/chat/sessions/${s.id}`)
    ElMessage.success('已删除')
    if (currentId.value === s.id) {
      currentId.value = null
      messages.value = []
    }
    loadSessions()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function onSend({ text, attachments = [] }) {
  if (sending.value || !currentId.value) return
  messages.value.push({ id: localSeq--, role: 'user', content: text, attachments })
  scrollToBottom()
  sending.value = true
  try {
    const res = await http.post(`/api/chat/sessions/${currentId.value}/send`, {
      text, page: '/ai-assistant', attachments: attachments.map((a) => a.id),
    })
    messages.value.push({
      id: res.message_id ?? localSeq--,
      role: 'assistant',
      content: res.content || '',
      card: res.card || null,
      confirm_token: res.confirm_token || null,
      confirm_status: res.confirm_status || '',
      provider: res.provider || '',
    })
    scrollToBottom()
    loadSessions() // 标题可能已更新
    inputRef.value?.clear()
  } catch (e) {
    messages.value.push({ id: localSeq--, role: 'assistant', content: `请求失败：${errMsg(e)}`, provider: '' })
    scrollToBottom()
  } finally {
    sending.value = false
  }
}

async function doConfirm(m) {
  m._acting = true
  try {
    const res = await http.post(`/api/chat/sessions/${currentId.value}/confirm`, {
      message_id: m.id,
      token: m.confirm_token,
    })
    m.confirm_status = 'confirmed'
    m.confirm_token = null
    messages.value.push({
      id: res.message_id ?? localSeq--,
      role: 'assistant',
      content: res.summary || '操作完成。',
      card: res.card || null,
      provider: 'confirm',
    })
    scrollToBottom()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    m._acting = false
  }
}

async function doCancel(m) {
  m._acting = true
  try {
    await http.post(`/api/chat/sessions/${currentId.value}/cancel`, {
      message_id: m.id,
      token: m.confirm_token,
    })
    m.confirm_status = 'expired'
    m.confirm_token = null
    ElMessage.info('已取消该操作')
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    m._acting = false
  }
}

onMounted(async () => {
  await loadSessions()
  if (sessions.value.length) {
    selectSession(sessions.value[0].id)
  } else {
    await newSession()
  }
  // 模块快捷入口预填指令（/ai-assistant?prefill=...）
  if (route.query.prefill) {
    inputRef.value?.setText(String(route.query.prefill))
  }
})

// 已停留在本页时，快捷入口跳转（仅 query 变化）也动态填充
watch(() => route.query.prefill, (v) => {
  if (v) inputRef.value?.setText(String(v))
})
</script>
