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
            <el-empty description="输入问题开始问数，例如：最近 7 天销售额趋势">
              <template #image><el-icon :size="60" color="var(--dp-border)"><ChatLineSquare /></el-icon></template>
            </el-empty>
          </div>

          <div v-for="m in messages" :key="m.id" class="chat-msg-row" :class="m.role">
            <template v-if="m.role === 'user'">
              <div class="chat-bubble-user">{{ m.content }}</div>
            </template>
            <template v-else>
              <div class="chat-bubble-ai">
                <div class="ai-card">
                  <div class="ai-content">{{ m.content }}</div>
                  <ChatCard v-if="m.card && m.card.type" :msg="m" @confirm="doConfirm(m)" @cancel="doCancel(m)" />
                  <div class="ai-meta" v-if="m.provider || m.ts">
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

      <div class="chat-input-area">
        <PromptChips v-if="currentId" page="/chat" @pick="input = $event" />
        <div style="display: flex; gap: 10px; align-items: flex-end">
          <el-input
            v-model="input" type="textarea" :autosize="{ minRows: 1, maxRows: 5 }" resize="none"
            placeholder="输入问题，Enter 发送，Shift+Enter 换行" :disabled="sending || !currentId"
            @keydown.enter.exact.prevent="send"
          />
          <el-button type="primary" size="large" :loading="sending" :disabled="!currentId || !input.trim()" @click="send">
            发送
          </el-button>
        </div>
        <div class="muted" style="margin-top: 6px">
          智能问数支持查数据 / 查表结构 / 创建同步任务 / 触发与暂停工作流 / 发布 API（高危操作需确认）
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import ChatCard from '../components/ChatCard.vue'
import PromptChips from '../components/PromptChips.vue'

const sessions = ref([])
const sessLoading = ref(false)
const creating = ref(false)
const currentId = ref(null)
const messages = ref([])
const msgLoading = ref(false)
const input = ref('')
const sending = ref(false)
const msgBox = ref(null)

let localSeq = -1

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

async function send() {
  const text = input.value.trim()
  if (!text || sending.value || !currentId.value) return
  input.value = ''
  messages.value.push({ id: localSeq--, role: 'user', content: text })
  scrollToBottom()
  sending.value = true
  try {
    const res = await http.post(`/api/chat/sessions/${currentId.value}/send`, { text, page: '/chat' })
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
})
</script>
