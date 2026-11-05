<template>
  <div>
    <div class="page-card">
      <el-tabs v-model="tab" @tab-change="onTab">
        <!-- ============ API 管理 ============ -->
        <el-tab-pane label="API 管理" name="apis">
          <div class="toolbar">
            <span class="muted">将 SQL 发布为带鉴权/限流/缓存的 HTTP 数据接口</span>
            <div class="spacer" />
            <span v-if="readonly" class="muted">只读模式（可查看文档）</span>
            <el-button icon="Refresh" circle @click="loadApis" />
            <el-button v-if="!readonly" type="primary" icon="Plus" @click="openApiDlg()">新建 API</el-button>
          </div>

          <el-table v-loading="apiLoading" :data="apis" stripe>
            <el-table-column prop="code" label="编码" min-width="150" class-name="mono">
              <template #default="{ row }"><span class="mono" style="font-weight: 600">{{ row.code }}</span></template>
            </el-table-column>
            <el-table-column prop="name" label="名称" min-width="170" />
            <el-table-column prop="datasource" label="数据源" width="120" />
            <el-table-column label="状态" width="95">
              <template #default="{ row }">
                <el-tag size="small" :type="apiStatusTag(row.status)">{{ apiStatusLabel(row.status) }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column label="参数" width="90">
              <template #default="{ row }">{{ (row.params || []).length }}</template>
            </el-table-column>
            <el-table-column label="缓存/上限" width="120">
              <template #default="{ row }">
                <span class="muted">{{ row.cache_ttl || 0 }}s / {{ row.row_limit }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="updated_at" label="更新时间" width="165">
              <template #default="{ row }"><span class="muted">{{ row.updated_at || '-' }}</span></template>
            </el-table-column>
            <el-table-column label="操作" width="300" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openDoc(row)">文档</el-button>
                <el-button v-if="!readonly" link type="primary" @click="openTest(row)">测试</el-button>
                <el-button v-if="!readonly && row.status === 'draft'" link type="primary" @click="openApiDlg(row)">编辑</el-button>
                <el-button v-if="!readonly && row.status !== 'published'" link type="success" @click="publishApi(row)">发布</el-button>
                <el-button v-if="!readonly && row.status === 'published'" link type="warning" @click="offlineApi(row)">下线</el-button>
                <el-button v-if="!readonly && row.status === 'draft'" link type="danger" @click="removeApi(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无 API" /></template>
          </el-table>
        </el-tab-pane>

        <!-- ============ 应用管理（admin） ============ -->
        <el-tab-pane v-if="auth.isAdmin" label="应用管理" name="apps">
          <div class="toolbar">
            <span class="muted">AK/SK 应用，调用方凭 app_key/app_secret 签名访问数据 API</span>
            <div class="spacer" />
            <el-button icon="Refresh" circle @click="loadApps" />
            <el-button type="primary" icon="Plus" @click="appDlg.visible = true">新建应用</el-button>
          </div>
          <el-table v-loading="appLoading" :data="apps" stripe>
            <el-table-column prop="app_name" label="应用" min-width="150" />
            <el-table-column label="App Key" min-width="220">
              <template #default="{ row }">
                <span class="mono">{{ row.app_key }}</span>
                <el-button link type="primary" icon="CopyDocument" @click="copyText(row.app_key)" />
              </template>
            </el-table-column>
            <el-table-column prop="rate_limit" label="限流(QPS)" width="100" />
            <el-table-column prop="ip_whitelist" label="IP 白名单" min-width="130">
              <template #default="{ row }">{{ row.ip_whitelist || '-' }}</template>
            </el-table-column>
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'active' ? 'success' : 'info'">
                  {{ row.status === 'active' ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="230" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openGrant(row)">授权 API</el-button>
                <el-button link type="primary" @click="viewGrants(row)">已授权</el-button>
                <el-button
                  link :type="row.status === 'active' ? 'warning' : 'success'"
                  @click="toggleApp(row)"
                >{{ row.status === 'active' ? '停用' : '启用' }}</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无应用" /></template>
          </el-table>
        </el-tab-pane>

        <!-- ============ 调用日志 ============ -->
        <el-tab-pane v-if="!readonly" label="调用日志" name="logs">
          <div class="toolbar">
            <el-select v-model="logApiId" placeholder="全部 API" clearable style="width: 220px" @change="loadLogs(1)">
              <el-option v-for="a in apis" :key="a.id" :label="a.code" :value="a.id" />
            </el-select>
            <el-button icon="Search" @click="loadLogs(1)">筛选</el-button>
            <div class="spacer" />
            <span class="muted">共 {{ logTotal }} 条</span>
            <el-button icon="Refresh" circle @click="loadLogs()" />
          </div>
          <el-table v-loading="logLoading" :data="logs" stripe>
            <el-table-column prop="id" label="#" width="70" />
            <el-table-column label="API" width="140">
              <template #default="{ row }">
                <span class="mono">{{ apiCode(row.api_id) }}</span>
              </template>
            </el-table-column>
            <el-table-column label="App Key" min-width="180">
              <template #default="{ row }"><span class="mono muted">{{ row.app_key }}</span></template>
            </el-table-column>
            <el-table-column label="参数" min-width="200" show-overflow-tooltip>
              <template #default="{ row }">
                <span class="mono muted">{{ JSON.stringify(row.params || {}) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="cost_ms" label="耗时" width="90">
              <template #default="{ row }">{{ row.cost_ms }} ms</template>
            </el-table-column>
            <el-table-column prop="row_count" label="行数" width="80" />
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <el-tag size="small" :type="row.status === 'ok' ? 'success' : 'danger'">{{ row.status }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="error" label="错误" min-width="140" show-overflow-tooltip>
              <template #default="{ row }">{{ row.error || '-' }}</template>
            </el-table-column>
            <el-table-column prop="ts" label="时间" width="165" />
            <template #empty><el-empty description="暂无调用日志" /></template>
          </el-table>
          <div style="display: flex; justify-content: flex-end; margin-top: 12px">
            <el-pagination
              v-model:current-page="logPage" :page-size="logSize" :total="logTotal"
              layout="total, prev, pager, next" background @current-change="loadLogs()"
            />
          </div>
        </el-tab-pane>

        <!-- ============ 脱敏规则 ============ -->
        <el-tab-pane label="脱敏规则" name="mask">
          <div class="toolbar">
            <span class="muted">列级出参脱敏：作用于数据服务 API、数据查询工作台与智能问数的查询结果（同步链路不受影响）</span>
            <div class="spacer" />
            <el-button v-if="auth.canWrite" type="primary" size="small" icon="Plus" @click="openMask()">新增规则</el-button>
            <el-button icon="Refresh" circle size="small" @click="loadMask" />
          </div>
          <el-table v-loading="maskLoading" :data="maskRules" stripe size="small" max-height="420">
            <el-table-column prop="name" label="名称" min-width="120" />
            <el-table-column prop="column_name" label="列名" min-width="120">
              <template #default="{ row }"><el-tag size="small" effect="plain" class="mono">{{ row.column_name }}</el-tag></template>
            </el-table-column>
            <el-table-column label="策略" width="120">
              <template #default="{ row }">
                <el-tag size="small" :type="{ phone: 'primary', id_card: 'danger', email: 'success', custom: 'warning' }[row.strategy]">
                  {{ { phone: '手机号', id_card: '证件号', email: '邮箱', custom: '自定义' }[row.strategy] }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="作用范围" width="160">
              <template #default="{ row }">{{ row.datasource_id === 0 ? '全部数据源' : dsNameOf(row.datasource_id) }}</template>
            </el-table-column>
            <el-table-column label="启用" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '启用' : '停用' }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column v-if="auth.canWrite" label="操作" width="140" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="openMask(row)">编辑</el-button>
                <el-button link type="danger" @click="removeMask(row)">删除</el-button>
              </template>
            </el-table-column>
            <template #empty><el-empty description="暂无脱敏规则" /></template>
          </el-table>

          <el-dialog v-model="maskDlg.visible" :title="maskDlg.isEdit ? '编辑脱敏规则' : '新增脱敏规则'" width="480px" destroy-on-close>
            <el-form :model="maskForm" label-width="110px">
              <el-form-item label="名称"><el-input v-model="maskForm.name" placeholder="例如：手机号掩码" /></el-form-item>
              <el-form-item label="作用数据源">
                <el-select v-model="maskForm.datasource_id" style="width: 100%">
                  <el-option :value="0" label="全部数据源" />
                  <el-option v-for="d in dsOptions" :key="d.id" :label="d.name" :value="d.id" />
                </el-select>
              </el-form-item>
              <el-form-item label="列名" required>
                <el-input v-model="maskForm.column_name" class="mono" placeholder="phone" />
              </el-form-item>
              <el-form-item label="策略" required>
                <el-select v-model="maskForm.strategy" style="width: 100%">
                  <el-option value="phone" label="手机号（138****5678）" />
                  <el-option value="id_card" label="证件号（前6后4）" />
                  <el-option value="email" label="邮箱（a***@dom）" />
                  <el-option value="custom" label="自定义正则替换为 ***" />
                </el-select>
              </el-form-item>
              <el-form-item v-if="maskForm.strategy === 'custom'" label="正则" required>
                <el-input v-model="maskForm.custom_regex" class="mono" placeholder="如 \d{4}$" />
              </el-form-item>
            </el-form>
            <template #footer>
              <el-button @click="maskDlg.visible = false">取消</el-button>
              <el-button type="primary" :loading="maskForm.saving" @click="saveMask">保存</el-button>
            </template>
          </el-dialog>
        </el-tab-pane>
      </el-tabs>
    </div>

    <!-- API 新建/编辑 -->
    <el-dialog v-model="apiDlg.visible" :title="apiDlg.isEdit ? '编辑 API' : '新建 API'" width="760px" top="5vh" destroy-on-close>
      <el-form :model="apiDlg.form" label-width="90px">
        <el-row :gutter="14">
          <el-col :span="8">
            <el-form-item label="编码" required>
              <el-input v-model="apiDlg.form.code" placeholder="如 get_user_list" :disabled="apiDlg.isEdit" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="名称" required><el-input v-model="apiDlg.form.name" /></el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="数据源" required>
              <el-select v-model="apiDlg.form.datasource_id" filterable style="width: 100%">
                <el-option v-for="d in dsList" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="SQL 模板" required>
          <el-input
            v-model="apiDlg.form.sql_template" type="textarea" :rows="4" class="mono"
            placeholder="只读 SELECT 语句，参数用 :name 占位，如 WHERE dt = :dt"
          />
        </el-form-item>

        <el-form-item label="参数列表">
          <div style="width: 100%">
            <div v-for="(p, i) in apiDlg.form.params" :key="i" style="display: flex; gap: 8px; margin-bottom: 8px">
              <el-input v-model="p.name" placeholder="参数名 name" style="width: 160px" size="small" />
              <el-select v-model="p.type" size="small" style="width: 110px">
                <el-option label="string" value="string" />
                <el-option label="number" value="number" />
                <el-option label="date" value="date" />
              </el-select>
              <el-checkbox v-model="p.required" label="必填" size="small" />
              <el-input v-model="p.default" placeholder="默认值" style="width: 140px" size="small" />
              <el-button size="small" icon="Delete" circle type="danger" plain @click="apiDlg.form.params.splice(i, 1)" />
            </div>
            <el-button size="small" icon="Plus" @click="apiDlg.form.params.push({ name: '', type: 'string', required: false, default: '' })">
              添加参数
            </el-button>
          </div>
        </el-form-item>

        <el-row :gutter="14">
          <el-col :span="8">
            <el-form-item label="缓存 TTL">
              <el-input-number v-model="apiDlg.form.cache_ttl" :min="0" :max="86400" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="行数上限">
              <el-input-number v-model="apiDlg.form.row_limit" :min="1" :max="100000" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="超时 ms">
              <el-input-number v-model="apiDlg.form.timeout_ms" :min="500" :max="60000" :step="500" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="apiDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="apiDlg.saving" @click="saveApi">保存</el-button>
      </template>
    </el-dialog>

    <!-- 测试执行 -->
    <el-dialog v-model="testDlg.visible" :title="`测试执行：${testDlg.api?.code || ''}`" width="760px" top="6vh">
      <div v-if="(testDlg.api?.params || []).length" style="margin-bottom: 14px">
        <div class="cc-sub">请求参数</div>
        <el-form inline>
          <el-form-item v-for="p in testDlg.api.params" :key="p.name" :label="p.name">
            <el-input v-model="testDlg.params[p.name]" :placeholder="p.default ? `默认 ${p.default}` : '值'" style="width: 180px" />
            <el-tag v-if="p.required" type="danger" size="small" style="margin-left: 4px">必填</el-tag>
          </el-form-item>
          <el-form-item>
            <el-button type="primary" :loading="testDlg.loading" @click="doTest">执行</el-button>
          </el-form-item>
        </el-form>
      </div>
      <div v-else style="margin-bottom: 14px">
        <el-button type="primary" :loading="testDlg.loading" @click="doTest">无参数，直接执行</el-button>
      </div>

      <div v-if="testDlg.loading" v-loading="true" style="height: 140px" />
      <template v-else-if="testDlg.result">
        <div style="display: flex; gap: 10px; margin-bottom: 10px">
          <el-tag size="small">{{ testDlg.result.row_count }} 行</el-tag>
          <el-tag v-if="testDlg.result.truncated" size="small" type="warning">已截断</el-tag>
          <el-tag v-if="testDlg.result.cached" size="small" type="success">命中缓存</el-tag>
        </div>
        <el-table :data="testRows" size="small" border max-height="360">
          <el-table-column
            v-for="(c, i) in testDlg.result.columns || []" :key="i" :label="String(c)" :prop="'c' + i"
            min-width="130" show-overflow-tooltip
          />
        </el-table>
      </template>
      <el-empty v-else description="填写参数后点击执行" :image-size="70" />
    </el-dialog>

    <!-- API 文档 -->
    <el-drawer v-model="docDlg.visible" :title="`API 文档：${docDlg.doc?.code || ''}`" size="560px">
      <template v-if="docDlg.doc">
        <el-alert :type="docDlg.doc.status === 'published' ? 'success' : 'warning'" :closable="false" style="margin-bottom: 14px"
          :title="docDlg.doc.status === 'published' ? '已发布，可对外调用' : '未发布（草稿/下线），仅可参考'" />
        <h4>请求地址</h4>
        <pre class="sql-pre">POST {{ docDlg.doc.path }}</pre>
        <h4>请求头</h4>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item v-for="(v, k) in docDlg.doc.headers || {}" :key="k" :label="k">
            <span class="mono">{{ v }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <h4 style="margin-top: 14px">请求参数（body.params）</h4>
        <el-descriptions :column="1" size="small" border>
          <el-descriptions-item v-for="(v, k) in docDlg.doc.body?.params || {}" :key="k" :label="k">
            <span class="mono">{{ v }}</span>
          </el-descriptions-item>
          <template v-if="!Object.keys(docDlg.doc.body?.params || {}).length">无参数</template>
        </el-descriptions>
        <h4 style="margin-top: 14px">签名算法</h4>
        <pre class="sql-pre">payload = {"app_key": ..., "timestamp": "秒级字符串", "params": {...}}
X-Sign = HMAC-SHA256(app_secret, json.dumps(payload, sort_keys=True, separators=(',',':')))</pre>
      </template>
      <div v-else v-loading="true" style="height: 300px" />
    </el-drawer>

    <!-- 新建应用 -->
    <el-dialog v-model="appDlg.visible" title="新建应用" width="460px" destroy-on-close>
      <el-form :model="appDlg.form" label-width="90px">
        <el-form-item label="应用名" required><el-input v-model="appDlg.form.app_name" /></el-form-item>
        <el-form-item label="限流 QPS"><el-input-number v-model="appDlg.form.rate_limit" :min="1" :max="1000" /></el-form-item>
        <el-form-item label="IP 白名单"><el-input v-model="appDlg.form.ip_whitelist" placeholder="逗号分隔，留空不限制" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="appDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="appDlg.saving" @click="createApp">创建</el-button>
      </template>
    </el-dialog>

    <!-- AK/SK 一次性展示 -->
    <el-dialog v-model="secretDlg.visible" title="应用创建成功（密钥仅展示一次）" width="560px" :close-on-click-modal="false">
      <el-alert type="warning" show-icon :closable="false" title="app_secret 仅此一次展示，请立即复制保存，关闭后无法再次查看" />
      <el-form label-width="90px" style="margin-top: 14px">
        <el-form-item label="App Key">
          <div style="display: flex; gap: 8px; width: 100%">
            <el-input :model-value="secretDlg.appKey" readonly class="mono" />
            <el-button icon="CopyDocument" @click="copyText(secretDlg.appKey)">复制</el-button>
          </div>
        </el-form-item>
        <el-form-item label="App Secret">
          <div style="display: flex; gap: 8px; width: 100%">
            <el-input :model-value="secretDlg.appSecret" readonly class="mono" />
            <el-button type="primary" icon="CopyDocument" @click="copyText(secretDlg.appSecret)">复制</el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button type="primary" @click="secretDlg.visible = false">我已保存</el-button>
      </template>
    </el-dialog>

    <!-- 授权 -->
    <el-dialog v-model="grantDlg.visible" :title="`授权 API：${grantDlg.app?.app_name || ''}`" width="460px">
      <el-form label-width="90px">
        <el-form-item label="选择 API" required>
          <el-select v-model="grantDlg.apiId" filterable placeholder="选择要授权的 API" style="width: 100%">
            <el-option v-for="a in apis" :key="a.id" :label="`${a.code}（${a.name}）`" :value="a.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="过期时间">
          <el-date-picker
            v-model="grantDlg.expireAt" type="datetime" placeholder="留空则永不过期"
            value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="grantDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="grantDlg.saving" @click="saveGrant">授权</el-button>
      </template>
    </el-dialog>

    <!-- 已授权列表 -->
    <el-dialog v-model="grantsDlg.visible" :title="`已授权 API：${grantsDlg.app?.app_name || ''}`" width="520px">
      <el-table :data="grantsDlg.list" size="small" v-loading="grantsDlg.loading">
        <el-table-column prop="code" label="编码" min-width="130" class-name="mono" />
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="expire_at" label="过期时间" width="180">
          <template #default="{ row }">{{ row.expire_at || '永久' }}</template>
        </el-table-column>
        <template #empty><el-empty description="暂无授权" :image-size="60" /></template>
      </el-table>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import { useAuthStore } from '../store'

const auth = useAuthStore()
const readonly = computed(() => auth.role === 'analyst')
const tab = ref('apis')

const apis = ref([])
const apiLoading = ref(false)
const dsList = ref([])

const apiDlg = reactive({
  visible: false, isEdit: false, id: null, saving: false,
  form: { code: '', name: '', description: '', datasource_id: null, sql_template: '', params: [], cache_ttl: 0, row_limit: 1000, timeout_ms: 5000 },
})

const testDlg = reactive({ visible: false, api: null, params: {}, loading: false, result: null })
const docDlg = reactive({ visible: false, doc: null })

const apps = ref([])
const appLoading = ref(false)
const appDlg = reactive({ visible: false, saving: false, form: { app_name: '', rate_limit: 10, ip_whitelist: '' } })
const secretDlg = reactive({ visible: false, appKey: '', appSecret: '' })

const grantDlg = reactive({ visible: false, app: null, apiId: null, expireAt: '', saving: false })
const grantsDlg = reactive({ visible: false, app: null, list: [], loading: false })

const logs = ref([])
const logTotal = ref(0)
const logPage = ref(1)
const logSize = 50
const logLoading = ref(false)
const logApiId = ref(null)

const testRows = computed(() =>
  (testDlg.result?.rows || []).map((r) => Object.fromEntries((r || []).map((v, i) => [`c${i}`, v])))
)

function apiStatusTag(s) {
  return { draft: 'info', published: 'success', offline: 'warning' }[s] || 'info'
}
function apiStatusLabel(s) {
  return { draft: '草稿', published: '已发布', offline: '已下线' }[s] || s
}
function apiCode(id) {
  return apis.value.find((a) => a.id === id)?.code || `#${id}`
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    const input = document.createElement('textarea')
    input.value = text
    document.body.appendChild(input)
    input.select()
    document.execCommand('copy')
    document.body.removeChild(input)
    ElMessage.success('已复制')
  }
}

async function loadApis() {
  apiLoading.value = true
  try {
    apis.value = await http.get('/api/apigw/apis')
  } catch { /* ignore */ } finally {
    apiLoading.value = false
  }
}

async function loadDs() {
  try {
    dsList.value = await http.get('/api/datasources')
  } catch { /* ignore */ }
}

function openApiDlg(row) {
  apiDlg.isEdit = !!row
  apiDlg.id = row?.id || null
  apiDlg.form = row
    ? {
        code: row.code, name: row.name, description: row.description || '',
        datasource_id: row.datasource_id, sql_template: row.sql_template,
        params: (row.params || []).map((p) => ({ name: p.name, type: p.type || 'string', required: !!p.required, default: p.default ?? '' })),
        cache_ttl: row.cache_ttl || 0, row_limit: row.row_limit || 1000, timeout_ms: row.timeout_ms || 5000,
      }
    : { code: '', name: '', description: '', datasource_id: null, sql_template: '', params: [], cache_ttl: 0, row_limit: 1000, timeout_ms: 5000 }
  apiDlg.visible = true
}

async function saveApi() {
  const f = apiDlg.form
  if (!f.code.trim() || !f.name.trim() || !f.datasource_id || !f.sql_template.trim()) {
    ElMessage.warning('请完整填写编码、名称、数据源与 SQL 模板')
    return
  }
  const payload = {
    ...f,
    code: f.code.trim(),
    params: f.params.filter((p) => p.name.trim()).map((p) => ({ name: p.name.trim(), type: p.type, required: !!p.required, default: p.default })),
  }
  apiDlg.saving = true
  try {
    if (apiDlg.isEdit) await http.put(`/api/apigw/apis/${apiDlg.id}`, payload)
    else await http.post('/api/apigw/apis', payload)
    ElMessage.success('已保存')
    apiDlg.visible = false
    loadApis()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    apiDlg.saving = false
  }
}

async function publishApi(row) {
  try {
    await http.post(`/api/apigw/apis/${row.id}/publish`)
    ElMessage.success(`API「${row.code}」已发布`)
    loadApis()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function offlineApi(row) {
  try {
    await http.post(`/api/apigw/apis/${row.id}/offline`)
    ElMessage.success('已下线')
    loadApis()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function removeApi(row) {
  try {
    await ElMessageBox.confirm(`删除 API「${row.code}」？相关授权将一并移除。`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/apigw/apis/${row.id}`)
    ElMessage.success('已删除')
    loadApis()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

function openTest(row) {
  testDlg.api = row
  testDlg.result = null
  testDlg.params = {}
  for (const p of row.params || []) {
    testDlg.params[p.name] = p.default ?? ''
  }
  testDlg.visible = true
}

async function doTest() {
  testDlg.loading = true
  try {
    const params = {}
    for (const p of testDlg.api.params || []) {
      const v = testDlg.params[p.name]
      if (v !== '' && v !== undefined && v !== null) params[p.name] = v
    }
    testDlg.result = await http.post(`/api/apigw/apis/${testDlg.api.id}/test-execute`, { params })
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    testDlg.loading = false
  }
}

async function openDoc(row) {
  docDlg.doc = null
  docDlg.visible = true
  try {
    docDlg.doc = await http.get(`/api/apigw/apis/${row.id}/doc`)
  } catch { /* ignore */ }
}

/* ---------- 应用管理 ---------- */
async function loadApps() {
  appLoading.value = true
  try {
    apps.value = await http.get('/api/apigw/apps')
  } catch { /* ignore */ } finally {
    appLoading.value = false
  }
}

async function createApp() {
  if (!appDlg.form.app_name.trim()) {
    ElMessage.warning('请填写应用名')
    return
  }
  appDlg.saving = true
  try {
    const res = await http.post('/api/apigw/apps', { ...appDlg.form })
    appDlg.visible = false
    appDlg.form = { app_name: '', rate_limit: 10, ip_whitelist: '' }
    secretDlg.appKey = res.app_key
    secretDlg.appSecret = res.app_secret
    secretDlg.visible = true
    loadApps()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    appDlg.saving = false
  }
}

async function toggleApp(row) {
  const target = row.status === 'active' ? 'disabled' : 'active'
  try {
    await http.put(`/api/apigw/apps/${row.id}/status`, { status: target })
    ElMessage.success(target === 'active' ? '已启用' : '已停用')
    loadApps()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

function openGrant(row) {
  grantDlg.app = row
  grantDlg.apiId = null
  grantDlg.expireAt = ''
  grantDlg.visible = true
}

async function saveGrant() {
  if (!grantDlg.apiId) {
    ElMessage.warning('请选择 API')
    return
  }
  grantDlg.saving = true
  try {
    await http.post(`/api/apigw/apps/${grantDlg.app.id}/grants`, {
      api_id: grantDlg.apiId,
      expire_at: grantDlg.expireAt || null,
    })
    ElMessage.success('授权成功')
    grantDlg.visible = false
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    grantDlg.saving = false
  }
}

async function viewGrants(row) {
  grantsDlg.app = row
  grantsDlg.list = []
  grantsDlg.visible = true
  grantsDlg.loading = true
  try {
    grantsDlg.list = await http.get(`/api/apigw/grants/${row.id}`)
  } catch { /* ignore */ } finally {
    grantsDlg.loading = false
  }
}

/* ---------- 调用日志 ---------- */
async function loadLogs(p) {
  if (p) logPage.value = p
  logLoading.value = true
  try {
    const res = await http.get('/api/apigw/logs', { params: { api_id: logApiId.value || 0, page: logPage.value, size: logSize } })
    logs.value = res.items
    logTotal.value = res.total
  } catch { /* ignore */ } finally {
    logLoading.value = false
  }
}

function onTab(name) {
  if (name === 'apps' && !apps.value.length) loadApps()
  if (name === 'logs' && !logs.value.length) loadLogs(1)
  if (name === 'mask') { loadMask(); loadDs() }
}

// ---- 脱敏规则 ----
const maskRules = ref([])
const maskLoading = ref(false)
const maskDlg = reactive({ visible: false, isEdit: false, id: null })
const maskForm = reactive({ name: '', datasource_id: 0, column_name: '',
                            strategy: 'phone', custom_regex: '', enabled: true, saving: false })
const dsOptions = computed(() => dsList.value)

function dsNameOf(id) {
  const d = dsList.value.find((x) => x.id === id)
  return d ? d.name : `#${id}`
}

async function loadMask() {
  maskLoading.value = true
  try { maskRules.value = await http.get('/api/mask/rules') } finally { maskLoading.value = false }
}

function openMask(row) {
  maskDlg.isEdit = !!row
  maskDlg.id = row?.id || null
  Object.assign(maskForm, row ? {
    name: row.name || '', datasource_id: row.datasource_id || 0,
    column_name: row.column_name, strategy: row.strategy,
    custom_regex: row.custom_regex || '', enabled: !!row.enabled, saving: false,
  } : { name: '', datasource_id: 0, column_name: '', strategy: 'phone',
        custom_regex: '', enabled: true, saving: false })
  maskDlg.visible = true
}

async function saveMask() {
  if (!maskForm.column_name.trim()) { ElMessage.warning('请填写列名'); return }
  maskForm.saving = true
  try {
    const payload = { name: maskForm.name, datasource_id: maskForm.datasource_id,
                      column_name: maskForm.column_name.trim(), strategy: maskForm.strategy,
                      custom_regex: maskForm.custom_regex, enabled: maskForm.enabled }
    if (maskDlg.isEdit) await http.put(`/api/mask/rules/${maskDlg.id}`, payload)
    else await http.post('/api/mask/rules', payload)
    ElMessage.success('已保存')
    maskDlg.visible = false
    loadMask()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    maskForm.saving = false
  }
}

async function removeMask(row) {
  try {
    await ElMessageBox.confirm(`删除脱敏规则「${row.name || row.column_name}」？`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/mask/rules/${row.id}`)
    ElMessage.success('已删除')
    loadMask()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(() => {
  loadApis()
  loadDs()
})
</script>
