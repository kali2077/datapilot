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
                  <template v-if="m.streaming">
                    <div v-for="(s, i) in m.stages" :key="'st' + i" class="stream-stage">
                      <el-icon class="is-loading"><Loading /></el-icon><span>{{ s }}</span>
                    </div>
                    <div v-if="m.content" class="ai-content stream-text">{{ m.content }}<span class="stream-cursor">▍</span></div>
                    <div v-else class="muted stream-text">思考中<span class="stream-cursor">…</span></div>
                  </template>
                  <template v-else>
                    <el-collapse v-if="m.tool_trace?.length" class="trace-collapse">
                      <el-collapse-item :title="`执行过程（${m.tool_trace.length} 步工具调用）`">
                        <div v-for="(t, ti) in m.tool_trace" :key="ti" class="trace-item">
                          <el-tag size="small" effect="plain" class="mono">{{ t.name }}</el-tag>
                          <span class="muted tiny">{{ t.summary }}</span>
                        </div>
                      </el-collapse-item>
                    </el-collapse>
                    <div class="ai-content md" v-html="renderMd(m.content)" />
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
                  </template>
                </div>
              </div>
            </template>
          </div>
        </template>
        <el-empty v-else description="选择或新建一个会话" />
      </div>

      <ChatInput ref="inputRef" :session-id="currentId" :sending="sending" :disabled="!currentId"
                 placeholder="输入问题，Enter 发送，Shift+Enter 换行"
                 hint="智能问数支持查数据 / 查表结构 / 创建同步任务 / 发布 API（高危操作需确认）"
                 @send="onSend">
        <template #top>
          <PromptChips v-if="currentId" page="/chat" @pick="inputRef?.setText($event)" />
        </template>
      </ChatInput>
    </div>
  </div>
</template>

<script setup>
import { nextTick, onMounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { DocumentCopy } from '@element-plus/icons-vue'
import MarkdownIt from 'markdown-it'
import http, { errMsg, nowTs, postSSE } from '../api'
import { copyText } from '../utils/clipboard'
import { useAuthStore } from '../store'
import ChatCard from '../components/ChatCard.vue'
import ChatInput from '../components/ChatInput.vue'
import PromptChips from '../components/PromptChips.vue'

const route = useRoute()
const auth = useAuthStore()

// Markdown 渲染（默认转义内联 HTML，防 XSS）；AI 回复正文用，流式增量仍为纯文本
const md = new MarkdownIt({ breaks: true, linkify: true })
// 链接新窗口打开，避免把用户带离对话页
const _defaultLink = md.renderer.rules.link_open
  || ((tokens, idx, opts) => md.renderer.renderToken(tokens, idx, opts))
md.renderer.rules.link_open = (tokens, idx, opts) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return _defaultLink(tokens, idx, opts)
}
function renderMd(text) {
  return md.render(text || '')
}
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
  // 流式占位气泡：阶段提示 + 增量正文，完成后替换为落库的正式消息
  const streamMsg = reactive({ id: localSeq--, role: 'assistant', streaming: true, content: '', stages: [] })
  messages.value.push(streamMsg)
  scrollToBottom()
  sending.value = true
  try {
    const done = await postSSE(`/api/chat/sessions/${currentId.value}/send_stream`, {
      text, page: '/chat', attachments: attachments.map((a) => a.id),
    }, (ev) => {
      if (ev.type === 'stage') streamMsg.stages.push(ev.text)
      else if (ev.type === 'delta') streamMsg.content += ev.text
      else if (ev.type === 'tool_begin') streamMsg.stages.push(`执行工具 ${ev.name}…`)
      else if (ev.type === 'tool_end') streamMsg.stages.push(`${ev.name}：${ev.summary || '完成'}`)
      scrollToBottom()
    })
    messages.value = messages.value.filter((m) => m !== streamMsg)
    messages.value.push({
      id: done.message_id ?? localSeq--,
      role: 'assistant',
      content: done.content || '',
      card: done.card || null,
      tool_trace: done.tool_trace || [],
      confirm_token: done.confirm_token || null,
      confirm_status: done.confirm_status || '',
      provider: done.provider || '',
      ts: nowTs(),
    })
    scrollToBottom()
    loadSessions() // 标题可能已更新
    inputRef.value?.clear()
  } catch (e) {
    messages.value = messages.value.filter((m) => m !== streamMsg)
    messages.value.push({ id: localSeq--, role: 'assistant', content: `请求失败：${e.message || errMsg(e)}`, provider: '' })
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
  applyPrefill()
})

// 场景按钮（AI 建同步/AI 诊断…）经 openAi() 带 ?prefill= 跳入，预填输入框不自动发送
function applyPrefill() {
  const pre = route.query.prefill
  if (pre) {
    inputRef.value?.setText(String(pre))
  }
}

watch(() => route.query.prefill, () => applyPrefill())
</script>

<style scoped>
.trace-collapse {
  margin-bottom: 8px;
  border: none;
}
.trace-collapse :deep(.el-collapse-item__header) {
  height: 30px;
  font-size: 12px;
  color: var(--dp-text-2);
}
.trace-collapse :deep(.el-collapse-item__wrap) {
  border-bottom: none;
}
.trace-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 2px 0;
}
.ai-content.md :deep(p) {
  margin: 0 0 8px;
}
.ai-content.md :deep(pre) {
  background: var(--dp-bg-2, #f6f7f9);
  padding: 8px 10px;
  border-radius: 6px;
  overflow-x: auto;
  font-size: 12.5px;
}
.ai-content.md :deep(code) {
  font-family: var(--font-mono, monospace);
  font-size: 12.5px;
}
.ai-content.md :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
}
.ai-content.md :deep(th),
.ai-content.md :deep(td) {
  border: 1px solid var(--dp-border, #e4e7ed);
  padding: 4px 8px;
  font-size: 12.5px;
}
</style>
