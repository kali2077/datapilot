<template>
  <div class="chat-input-area">
    <slot name="top" />
    <!-- 附件预览条(一等公民:图片缩略图 / 表格·文档·代码 chip,均可移除) -->
    <div v-if="atts.length" class="att-row">
      <div v-for="a in atts" :key="a.key" class="att-chip" :class="{ uploading: a.uploading }">
        <el-image v-if="a.type === 'image'" class="att-thumb" :src="a.preview" fit="cover" />
        <el-icon v-else class="att-file-icon"><component :is="kindIcon(a.type)" /></el-icon>
        <span class="att-name" :title="a.name">{{ a.name }}</span>
        <span class="att-size">{{ fmtSize(a.size) }}</span>
        <span v-if="a.meta?.rows != null" class="att-size">{{ a.meta.rows }} 行</span>
        <span v-else-if="a.meta?.pages != null" class="att-size">{{ a.meta.pages }} 页</span>
        <el-icon v-if="a.uploading" class="att-loading is-loading"><Loading /></el-icon>
        <el-icon v-else class="att-remove" title="移除" @click="remove(a.key)"><CircleCloseFilled /></el-icon>
      </div>
    </div>

    <el-input
      ref="inputRef" v-model="text" type="textarea" :autosize="{ minRows: 1, maxRows: 5 }" resize="none"
      :placeholder="placeholder" :disabled="disabled || sending"
      @keydown.enter.exact.prevent="send" @paste="onPaste"
    />
    <div class="chat-input-actions">
      <el-tooltip content="上传附件：图片 / xlsx / pdf / docx / sql / yaml / csv…（最多 4 个；文本 ≤2MB，其余 ≤10MB）" placement="top">
        <el-button text :icon="Paperclip" aria-label="添加附件" :disabled="disabled || sending" @click="fileRef?.click()" />
      </el-tooltip>
      <input ref="fileRef" type="file" multiple hidden :accept="ACCEPT" @change="onFiles" />
      <span class="tiny">{{ hint }}</span>
      <span class="flex-1" />
      <el-button type="primary" :loading="sending"
                  :disabled="disabled || (!text.trim() && !readyAtts.length)" @click="send">
        发送
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Paperclip } from '@element-plus/icons-vue'
import http, { errMsg } from '../api'

const props = defineProps({
  sessionId: { type: Number, default: null },
  sending: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  placeholder: { type: String, default: '输入问题，Enter 发送，Shift+Enter 换行' },
  hint: { type: String, default: 'Enter 发送 · Shift+Enter 换行 · 支持粘贴截图' },
})
const emit = defineEmits(['send'])

const ACCEPT = 'image/png,image/jpeg,image/webp,image/gif,.txt,.md,.csv,.tsv,.json,.log,' +
  '.sql,.py,.sh,.yaml,.yml,.xml,.html,.ini,.conf,.toml,.xlsx,.pdf,.docx'
const MAX_ATTS = 4
const TEXT_LIKE = /\.(txt|md|csv|tsv|json|log|sql|py|sh|ya?ml|xml|html|ini|conf|toml)$/i

const inputRef = ref(null)
const fileRef = ref(null)
const text = ref('')
const atts = ref([]) // {key, id, name, size, type, preview, url, meta, uploading}
let seq = 0

const readyAtts = computed(() => atts.value.filter((a) => a.id && !a.uploading))

function fmtSize(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function kindIcon(t) {
  return { table: 'Grid', doc: 'Reading' }[t] || 'Document'
}

function addFiles(files) {
  for (const f of files) {
    if (atts.value.length >= MAX_ATTS) {
      ElMessage.warning(`一条消息最多 ${MAX_ATTS} 个附件`)
      break
    }
    const cap = TEXT_LIKE.test(f.name) ? 2 : 10
    if (f.size > cap * 1024 * 1024) {
      ElMessage.error(`「${f.name}」超过 ${cap}MB 限制`)
      continue
    }
    const isImg = (f.type || '').startsWith('image/')
    // reactive 包装:上传回调里直接改属性必须走代理,否则不触发响应式更新
    const entry = reactive({ key: ++seq, id: null, name: f.name, size: f.size,
                             type: isImg ? 'image' : 'text', meta: null,
                             preview: isImg ? URL.createObjectURL(f) : '', url: '', uploading: true })
    atts.value.push(entry)
    const fd = new FormData()
    fd.append('file', f)
    http.post(`/api/chat/sessions/${props.sessionId}/attachments`, fd)
      .then((res) => {
        entry.id = res.id
        entry.url = res.url
        entry.type = res.type || entry.type
        entry.meta = res.meta || null
        entry.uploading = false
      })
      .catch((e) => {
        atts.value = atts.value.filter((x) => x.key !== entry.key)
        ElMessage.error(`「${f.name}」上传失败：${errMsg(e)}`)
      })
  }
}

function onFiles(e) {
  addFiles([...e.target.files])
  e.target.value = ''
}

// 剪贴板:① 图片文件 → 附件;② 多行含制表符的文本 → 视为从 Excel 复制的表格,转 CSV 附件;
// ③ 普通文本走原生粘贴不拦截
function onPaste(e) {
  const files = [...(e.clipboardData?.files || [])]
  if (files.length) {
    e.preventDefault()
    addFiles(files)
    return
  }
  const txt = e.clipboardData?.getData('text/plain') || ''
  if (txt.includes('\t') && txt.trimEnd().includes('\n')) {
    e.preventDefault()
    const csv = tsvToCsv(txt)
    addFiles([new File([csv], '粘贴的表格.csv', { type: 'text/csv' })])
  }
}

function tsvToCsv(tsv) {
  return tsv.replace(/\r/g, '').trimEnd().split('\n')
    .map((line) => line.split('\t')
      .map((c) => (/^[",\n]/.test(c) || c.includes('"') || c.includes(',')
        ? `"${c.replace(/"/g, '""')}"` : c))
      .join(','))
    .join('\n')
}

function remove(key) {
  const a = atts.value.find((x) => x.key === key)
  if (a?.preview) URL.revokeObjectURL(a.preview)
  atts.value = atts.value.filter((x) => x.key !== key)
}

function send() {
  if (props.sending || props.disabled) return
  if (atts.value.some((a) => a.uploading)) {
    ElMessage.warning('附件还在上传中，稍候')
    return
  }
  const t = text.value.trim()
  if (!t && !readyAtts.value.length) return
  emit('send', { text: t, attachments: readyAtts.value.map(({ key, preview, uploading, ...rest }) => rest) })
}

function setText(t) {
  text.value = String(t ?? '')
}

function clear() {
  text.value = ''
  atts.value.forEach((a) => a.preview && URL.revokeObjectURL(a.preview))
  atts.value = []
}

defineExpose({ clear, setText })
</script>

<style scoped>
.flex-1 {
  flex: 1;
}
</style>
