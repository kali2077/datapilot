<template>
  <div ref="el" class="sql-editor-monaco" :style="{ height: height + 'px' }" />
</template>

<script setup>
// SQL 编辑器（Monaco）：SQL 高亮 + 基于平台元数据的 schema-aware 补全（借鉴 Chat2DB 体验，
// 补全数据来自 /api/meta/schema —— 表/字段/类型/注释），Ctrl/Cmd+Enter 触发执行。
// 仅加载 core + SQL 语言贡献点，worker 走 Vite ?worker 本地打包（离线可用，不走 CDN）。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api'
import 'monaco-editor/esm/vs/editor/editor.all.js'
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution.js'
import EditorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import { onThemeChange } from '../charts/theme'

const props = defineProps({
  value: { type: String, default: '' },
  schema: { type: Array, default: () => [] },   // [{name, comment, columns:[{name,type,comment}]}]
  height: { type: Number, default: 220 },
  readOnly: { type: Boolean, default: false },
})
const emit = defineEmits(['update:value', 'run'])

const el = ref(null)
let editor = null
let offTheme = null

self.MonacoEnvironment = { getWorker: () => new EditorWorker() }

// ---------------- schema-aware 补全（模块级注册一次，schema 由组件实例推入） ----------------

const KEYWORDS = [
  'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING', 'LIMIT', 'JOIN', 'LEFT JOIN',
  'INNER JOIN', 'ON', 'AS', 'AND', 'OR', 'NOT', 'IN', 'BETWEEN', 'LIKE', 'IS NULL', 'DISTINCT',
  'COUNT(*)', 'SUM()', 'AVG()', 'MAX()', 'MIN()', 'CASE WHEN', 'WITH', 'UNION ALL', 'DESC', 'ASC',
]

const schemaHolder = { tables: [] }
let providerRegistered = false

// 注释来自数据库 comment/人工补录（用户可控输入）：Monaco 建议组件按 Markdown 渲染
// 且支持 HTML，必须去除 < > 防止存储型 XSS
const toDoc = (s) => String(s || '').replace(/[<>]/g, '')

function registerCompletion() {
  if (providerRegistered) return
  providerRegistered = true
  monaco.languages.registerCompletionItemProvider('sql', {
    triggerCharacters: ['.'],
    provideCompletionItems(model, position) {
      const tables = schemaHolder.tables || []
      const word = model.getWordUntilPosition(position)
      const range = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      }
      const linePrefix = model.getValueInRange({
        startLineNumber: position.lineNumber, startColumn: 1, endColumn: position.column,
      })
      const suggestions = []
      const K = monaco.languages.CompletionItemKind
      const dotMatch = /([a-zA-Z_][\w]*)\.\w*$/.exec(linePrefix)
      if (dotMatch) {
        // 表名. 前缀 → 只补该表的字段
        const tb = tables.find((t) => t.name.toLowerCase() === dotMatch[1].toLowerCase())
        for (const c of tb?.columns || []) {
          suggestions.push({ label: c.name, kind: K.Field, detail: c.type,
                             documentation: toDoc(c.comment), insertText: c.name, range })
        }
      } else {
        for (const t of tables) {
          suggestions.push({ label: t.name, kind: K.Class, detail: toDoc(t.comment),
                             documentation: toDoc(t.comment), insertText: t.name, range })
        }
        // 全部字段（detail 标注所属表，避免跨表重名混淆）；超大 schema 降级为只补表名
        const colCount = tables.reduce((s, t) => s + (t.columns?.length || 0), 0)
        if (colCount <= 2000) {
          for (const t of tables) {
            for (const c of t.columns || []) {
              suggestions.push({ label: c.name, kind: K.Field, detail: `${t.name} · ${c.type}`,
                                 documentation: toDoc(c.comment), insertText: c.name, range })
            }
          }
        }
        for (const kw of KEYWORDS) {
          suggestions.push({ label: kw, kind: K.Keyword, insertText: kw, range })
        }
      }
      return { suggestions }
    },
  })
}

// ---------------- 主题（跟随平台 light/dark token） ----------------

function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  return v || fallback
}

function defineThemes() {
  monaco.editor.defineTheme('dp-light', {
    base: 'vs', inherit: true, rules: [],
    colors: {
      'editor.background': cssVar('--dp-surface', '#ffffff'),
      'editor.foreground': cssVar('--dp-text-1', '#101828'),
      'editorLineNumber.foreground': cssVar('--dp-text-3', '#667085'),
      'editorLineNumber.activeForeground': cssVar('--dp-accent', '#2563eb'),
      'editor.selectionBackground': cssVar('--dp-accent-soft', '#2563eb22'),
      'editor.lineHighlightBackground': cssVar('--dp-bg-subtle', '#f8fafc'),
      'editorGutter.background': cssVar('--dp-surface', '#ffffff'),
      'editorWidget.background': cssVar('--dp-surface', '#ffffff'),
      'editorWidget.border': cssVar('--dp-border', '#e4e7ec'),
      'editorSuggestWidget.selectedBackground': cssVar('--dp-accent-soft', '#2563eb22'),
      'scrollbarSlider.background': cssVar('--dp-border', '#e4e7ec66'),
    },
  })
  monaco.editor.defineTheme('dp-dark', {
    base: 'vs-dark', inherit: true, rules: [],
    colors: {
      'editor.background': cssVar('--dp-surface', '#1a2231'),
      'editor.foreground': cssVar('--dp-text-1', '#e6e9ef'),
      'editorLineNumber.foreground': cssVar('--dp-text-3', '#77818f'),
      'editorLineNumber.activeForeground': cssVar('--dp-accent', '#60a5fa'),
      'editor.selectionBackground': cssVar('--dp-accent-soft', '#2563eb33'),
      'editor.lineHighlightBackground': cssVar('--dp-bg-subtle', '#222b3d'),
      'editorGutter.background': cssVar('--dp-surface', '#1a2231'),
      'editorWidget.background': cssVar('--dp-surface', '#1a2231'),
      'editorWidget.border': cssVar('--dp-border', '#2a3547'),
      'editorSuggestWidget.selectedBackground': cssVar('--dp-accent-soft', '#2563eb33'),
      'scrollbarSlider.background': cssVar('--dp-border', '#2a354766'),
    },
  })
}

function applyTheme() {
  defineThemes()
  const dark = document.documentElement.classList.contains('dark')
  monaco.editor.setTheme(dark ? 'dp-dark' : 'dp-light')
}

// ---------------- 组件生命周期 ----------------

onMounted(() => {
  registerCompletion()
  schemaHolder.tables = props.schema || []
  editor = monaco.editor.create(el.value, {
    value: props.value || '',
    language: 'sql',
    theme: document.documentElement.classList.contains('dark') ? 'dp-dark' : 'dp-light',
    minimap: { enabled: false },
    fontSize: 13,
    fontFamily: cssVar('--dp-mono', "'JetBrains Mono', monospace"),
    lineNumbersMinChars: 3,
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    automaticLayout: true,
    tabSize: 2,
    quickSuggestions: true,
    suggestOnTriggerCharacters: true,
    readOnly: props.readOnly,
    scrollbar: { verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
    padding: { top: 8, bottom: 8 },
  })
  editor.onDidChangeModelContent(() => emit('update:value', editor.getValue()))
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter, () => emit('run'))
  applyTheme()
  offTheme = onThemeChange(applyTheme)
})

watch(() => props.value, (v) => {
  // 外部赋值（历史/保存/助写回填）才整体 setValue，避免打断自身输入的光标
  if (editor && v !== editor.getValue()) editor.setValue(v || '')
})

watch(() => props.schema, (s) => { schemaHolder.tables = s || [] }, { deep: true })

watch(() => props.readOnly, (ro) => editor?.updateOptions({ readOnly: ro }))

onBeforeUnmount(() => {
  if (offTheme) offTheme()
  editor?.dispose()
  editor = null
})

function focus() {
  editor?.focus()
}

defineExpose({ focus })
</script>

<style scoped>
.sql-editor-monaco {
  width: 100%;
  border: 1px solid var(--dp-border);
  border-radius: 8px;
  overflow: hidden;
}
</style>
