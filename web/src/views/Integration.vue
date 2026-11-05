<template>
  <div>
    <div class="page-card">
      <div class="toolbar">
        <h3 class="page-title" style="margin: 0"><el-icon><Connection /></el-icon>{{ pageTitle }}</h3>
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 140px" @change="load">
          <el-option label="草稿" value="draft" />
          <el-option label="已发布" value="published" />
          <el-option label="已下线" value="offline" />
        </el-select>
        <div class="spacer" />
        <span v-if="readonly" class="muted">只读模式：查看集成任务与查询链路</span>
        <el-button icon="Refresh" circle @click="load" />
        <el-button icon="Collection" @click="openMappings">类型映射规则</el-button>
        <el-button icon="Files" @click="bulkDlg.visible = true">整库同步</el-button>
        <el-button icon="Upload" @click="openImport">文件导入</el-button>
        <el-button v-if="!readonly" icon="ChatDotRound" @click="openAi('帮我创建一个数据同步任务，例如：把 demo_source 的 orders 表每天凌晨2点同步到 demo_warehouse 的 ods_orders')">AI 建同步</el-button>
        <el-button v-if="!readonly" type="primary" icon="Plus" @click="openCreate">新建任务</el-button>
      </div>

      <el-table v-loading="loading" :data="list" stripe>
        <el-table-column prop="name" label="任务名称" min-width="160">
          <template #default="{ row }"><span style="font-weight: 600">{{ row.name }}</span></template>
        </el-table-column>
        <el-table-column label="源 → 目标" min-width="280">
          <template #default="{ row }">
            <span class="mono">
              <el-tag :type="dsTag(row.source?.type)" size="small" effect="plain">{{ row.source?.name || '?' }}</el-tag>
              {{ row.source?.table || row.source?.query || '?' }}
              <el-icon style="color: #98a2b3; margin: 0 4px; vertical-align: -3px"><Right /></el-icon>
              <el-tag :type="dsTag(row.target?.type)" size="small" effect="plain">{{ row.target?.name || '?' }}</el-tag>
              {{ row.target?.table || '?' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="statusTag(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="写入模式" width="120">
          <template #default="{ row }">
            <span class="muted">{{ writeModeLabel(row.config?.write_mode) }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="current_version" label="版本" width="80">
          <template #default="{ row }">v{{ row.current_version ?? '-' }}</template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="170">
          <template #default="{ row }"><span class="muted">{{ row.updated_at || '-' }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="380" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="validate(row)">校验</el-button>
            <el-button v-if="!readonly" link type="primary" @click="trial(row)">试跑</el-button>
            <el-button v-if="!readonly && row.status !== 'published'" link type="success" @click="openPublish(row)">发布</el-button>
            <el-button v-if="!readonly && row.status === 'published'" link type="warning" @click="offline(row)">下线</el-button>
            <el-button v-if="!readonly && row.status === 'draft'" link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link type="info" @click="openVersions(row)">版本</el-button>
            <el-button link type="success" @click="compareJob(row)">比对</el-button>
            <el-button v-if="!readonly && row.status !== 'published'" link type="danger" @click="removeJob(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无集成任务" /></template>
      </el-table>
    </div>

    <!-- 新建/编辑对话框 -->
    <el-dialog v-model="dlg.visible" :title="dlg.isEdit ? '编辑集成任务' : '新建集成任务'" width="720px" destroy-on-close top="6vh">
      <el-form :model="form" label-width="90px">
        <el-form-item label="任务名称" required>
          <el-input v-model="form.name" placeholder="如：ods_user_sync" />
        </el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
            <div class="col-head"><el-tag effect="dark" size="small">源</el-tag></div>
            <el-form-item label="数据源" required>
              <el-select v-model="form.source_datasource_id" filterable style="width: 100%">
                <el-option v-for="d in dsList" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="源表" required>
              <el-input v-model="form.source_table" placeholder="表名" />
            </el-form-item>
            <el-form-item label="查询SQL">
              <el-input v-model="form.source_query" type="textarea" :rows="2" class="mono" placeholder="可选：自定义抽取 SQL（填写后优先于源表）" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <div class="col-head"><el-tag effect="dark" size="small" type="success">目标</el-tag></div>
            <el-form-item label="数据源" required>
              <el-select v-model="form.target_datasource_id" filterable style="width: 100%">
                <el-option v-for="d in dsList" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="目标表" required>
              <el-input v-model="form.target_table" placeholder="表名" />
            </el-form-item>
            <el-form-item label="写入模式" required>
              <el-select v-model="form.write_mode" style="width: 100%">
                <el-option label="append（追加）" value="append" />
                <el-option label="truncate_insert（清空后写入）" value="truncate_insert" />
                <el-option label="replace（整表替换）" value="replace" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-divider style="margin: 4px 0 14px" />
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="增量同步">
              <el-switch v-model="form.increment_enabled" />
            </el-form-item>
          </el-col>
          <el-col :span="10">
            <el-form-item label="增量列">
              <el-input v-model="form.increment_column" :disabled="!form.increment_enabled" placeholder="如：updated_at / id" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="前置SQL">
              <el-input v-model="form.pre_sql" type="textarea" :rows="2" class="mono" placeholder="写入前执行（可选）" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="后置SQL">
              <el-input v-model="form.post_sql" type="textarea" :rows="2" class="mono" placeholder="写入后执行（可选）" />
            </el-form-item>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="dlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="save">保存</el-button>
      </template>
    </el-dialog>

    <!-- 校验结果 -->
    <el-dialog v-model="valDlg.visible" title="任务校验结果" width="600px">
      <div v-if="valDlg.loading" v-loading="true" style="height: 120px" />
      <template v-else>
        <el-alert
          :type="valDlg.ok ? 'success' : 'error'" :closable="false" show-icon style="margin-bottom: 12px"
          :title="valDlg.ok ? '校验通过，可以发布' : '校验未通过，存在 error 级问题'"
        />
        <el-alert
          v-for="(is, i) in valDlg.issues" :key="i" style="margin-bottom: 8px" :closable="false" show-icon
          :type="is.level === 'error' ? 'error' : is.level === 'warning' ? 'warning' : 'info'"
          :title="`[${is.level}] ${is.message}`"
        />
        <el-empty v-if="!valDlg.issues.length" description="无校验问题" :image-size="60" />
        <template v-if="valDlg.ddl">
          <div class="cc-sub">建议目标表 DDL</div>
          <pre class="sql-pre">{{ valDlg.ddl }}</pre>
        </template>
      </template>
    </el-dialog>

    <!-- 试跑结果 -->
    <el-dialog v-model="trialDlg.visible" title="试跑结果（100 行）" width="820px" top="6vh">
      <div v-if="trialDlg.loading" v-loading="true" style="height: 160px" />
      <template v-else>
        <el-descriptions :column="4" size="small" border style="margin-bottom: 12px">
          <el-descriptions-item label="读取行数">{{ trialDlg.stats?.read_rows ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="写入行数">{{ trialDlg.stats?.written_rows ?? '-' }}</el-descriptions-item>
          <el-descriptions-item label="耗时">{{ trialDlg.stats?.duration_ms ?? '-' }} ms</el-descriptions-item>
          <el-descriptions-item label="自动建表">
            <el-tag :type="trialDlg.stats?.auto_created ? 'success' : 'info'" size="small">
              {{ trialDlg.stats?.auto_created ? '是' : '否（表已存在）' }}
            </el-tag>
          </el-descriptions-item>
        </el-descriptions>
        <el-table :data="trialRows" size="small" border max-height="380">
          <el-table-column
            v-for="(c, i) in trialDlg.columns || []" :key="i" :label="String(c)" :prop="'c' + i"
            min-width="130" show-overflow-tooltip
          />
        </el-table>
        <div class="muted" style="margin-top: 8px">预览 {{ trialRows.length }} 行</div>
      </template>
    </el-dialog>

    <!-- 发布 -->
    <el-dialog v-model="pubDlg.visible" title="发布为调度工作流" width="480px">
      <el-form label-width="90px">
        <el-form-item label="Cron" required>
          <el-input v-model="pubDlg.cron" placeholder="0 2 * * *" class="mono" />
          <div class="muted">5 段 cron 表达式（分 时 日 月 周）</div>
        </el-form-item>
        <el-form-item label="依赖工作流">
          <el-select v-model="pubDlg.depends_on" multiple clearable placeholder="可多选" style="width: 100%">
            <el-option v-for="w in workflows" :key="w.id" :label="w.name" :value="w.id" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="pubDlg.visible = false">取消</el-button>
        <el-button type="primary" :loading="pubDlg.saving" @click="doPublish">发布</el-button>
      </template>
    </el-dialog>

    <!-- 版本抽屉 -->
    <el-drawer v-model="verDlg.visible" :title="`版本历史：${verDlg.job?.name || ''}`" size="560px">
      <el-table :data="verDlg.versions" size="small" v-loading="verDlg.loading">
        <el-table-column prop="version" label="版本" width="70">
          <template #default="{ row }">v{{ row.version }}</template>
        </el-table-column>
        <el-table-column prop="created_by" label="发布人" width="110" />
        <el-table-column prop="created_at" label="发布时间" min-width="160" />
        <el-table-column label="操作" width="170">
          <template #default="{ row }">
            <el-button link type="primary" @click="viewDatax(row)">查看 DataX</el-button>
            <el-button
              v-if="!readonly && verDlg.job && verDlg.job.status !== 'published' && row.version !== verDlg.job.current_version"
              link type="warning" @click="rollback(row)"
            >回滚至此</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无版本" :image-size="70" /></template>
      </el-table>
      <template v-if="verDlg.datax">
        <div class="cc-sub" style="display: flex; align-items: center; gap: 8px">
          DataX JSON（v{{ verDlg.dataxVersion }}）
          <el-button link type="primary" size="small" icon="CopyDocument" @click="copyText(verDlg.datax, 'DataX JSON')">复制</el-button>
        </div>
        <el-input :model-value="verDlg.datax" type="textarea" :rows="18" readonly class="mono" />
      </template>
    </el-drawer>

    <!-- 类型映射规则（同步任务字段类型转换的覆盖规则；admin 可编辑） -->
    <el-dialog v-model="mapDlg.visible" title="类型映射规则" width="760px" top="6vh">
      <div class="toolbar" style="margin-bottom: 8px">
        <span class="muted">同步任务自动映射字段类型时的覆盖规则（优先于内置通用映射）；
          {{ auth.isAdmin ? '' : '仅管理员可编辑，当前只读' }}</span>
        <div class="spacer" />
        <el-button icon="Refresh" circle @click="loadMappings" />
        <el-button v-if="auth.isAdmin" type="primary" size="small" icon="Plus" @click="openMapForm()">新增映射</el-button>
      </div>
      <el-table v-loading="mapLoading" :data="mappings" stripe size="small" max-height="420">
        <el-table-column prop="source_engine" label="源引擎" width="90" />
        <el-table-column label="源类型" min-width="120">
          <template #default="{ row }"><el-tag size="small" effect="plain" class="mono">{{ row.source_dtype }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="target_engine" label="目标引擎" width="100" />
        <el-table-column label="目标类型" min-width="120">
          <template #default="{ row }"><el-tag size="small" type="success" effect="plain" class="mono">{{ row.target_dtype }}</el-tag></template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip>
          <template #default="{ row }">{{ row.note || '-' }}</template>
        </el-table-column>
        <el-table-column v-if="auth.isAdmin" label="操作" width="80" fixed="right">
          <template #default="{ row }">
            <el-button link type="danger" @click="removeMapping(row)">删除</el-button>
          </template>
        </el-table-column>
        <template #empty><el-empty description="暂无自定义映射（全部走内置通用映射）" /></template>
      </el-table>

      <el-dialog v-model="mapForm.visible" title="新增映射" width="480px" append-to-body destroy-on-close>
        <el-form :model="mapForm" label-width="90px">
          <el-row :gutter="10">
            <el-col :span="12">
              <el-form-item label="源引擎">
                <el-input v-model="mapForm.source_engine" placeholder="* 或 mysql" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="目标引擎">
                <el-input v-model="mapForm.target_engine" placeholder="* 或 doris" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-row :gutter="10">
            <el-col :span="12">
              <el-form-item label="源类型" required>
                <el-input v-model="mapForm.source_dtype" placeholder="VARCHAR" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="目标类型" required>
                <el-input v-model="mapForm.target_dtype" placeholder="STRING" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="备注"><el-input v-model="mapForm.note" /></el-form-item>
        </el-form>
        <template #footer>
          <el-button @click="mapForm.visible = false">取消</el-button>
          <el-button type="primary" :loading="mapForm.saving" @click="saveMapping">保存</el-button>
        </template>
      </el-dialog>
    </el-dialog>

    <!-- 整库同步 -->
    <el-dialog v-model="bulkDlg.visible" title="整库同步" width="640px" destroy-on-close top="5vh">
      <el-form :model="bulkForm" label-width="110px">
        <el-form-item label="源数据源" required>
          <el-select v-model="bulkForm.source_datasource_id" filterable style="width: 100%" @change="loadBulkTables">
            <el-option v-for="d in dsListAll" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标数据源" required>
          <el-select v-model="bulkForm.target_datasource_id" filterable style="width: 100%">
            <el-option v-for="d in dsListAll" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="同步表" required>
          <el-select v-model="bulkForm.tables" multiple filterable style="width: 100%" placeholder="选择源库中的表">
            <el-option v-for="t in bulkTables" :key="t" :label="t" :value="t" />
          </el-select>
          <div class="muted" style="font-size: 12px; margin-top: 4px">
            来自源数据源元数据；每张表生成一个独立同步任务
          </div>
        </el-form-item>
        <el-form-item label="写入模式">
          <el-radio-group v-model="bulkForm.write_mode">
            <el-radio value="append">追加</el-radio>
            <el-radio value="truncate_insert">清空后写</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="调度">
          <el-input v-model="bulkForm.cron" placeholder="0 2 * * *（留空=手动）" style="width: 220px" />
        </el-form-item>
        <el-form-item label="发布">
          <el-switch v-model="bulkForm.publish" />
          <span class="muted" style="margin-left: 10px; font-size: 12px">校验+试跑通过后自动发布并注册调度</span>
        </el-form-item>
      </el-form>
      <div v-if="bulkResult" class="page-card" style="margin-top: 10px">
        <div style="font-weight: 600; margin-bottom: 6px">执行结果</div>
        <div v-for="c in bulkResult.created" :key="c.table" class="mono tiny" style="color: #67c23a">
          ✅ {{ c.table }} → 任务 #{{ c.job_id }}（v{{ c.version }}，{{ c.status }}）
        </div>
        <div v-for="c in bulkResult.skipped" :key="'s' + c.table" class="mono tiny" style="color: #98a2b3">
          ⏭ {{ c.table }}：{{ c.reason }}
        </div>
        <div v-for="f in bulkResult.failed" :key="'f' + f.table" class="mono tiny" style="color: #f56c6c">
          ❌ {{ f.table }}：{{ f.error }}
        </div>
      </div>
      <template #footer>
        <el-button @click="bulkDlg.visible = false">关闭</el-button>
        <el-button type="primary" :loading="bulkDlg.saving" :disabled="!bulkForm.tables.length"
                   @click="runBulk">开始批量同步</el-button>
      </template>
    </el-dialog>

    <!-- 数据比对 -->
    <el-dialog v-model="cmpDlg.visible" :title="`数据比对：${cmpDlg.jobName}`" width="640px">
      <template v-if="cmpDlg.report">
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="源行数">{{ cmpDlg.report.source.rows }}</el-descriptions-item>
          <el-descriptions-item label="目标行数">{{ cmpDlg.report.target.rows }}</el-descriptions-item>
          <el-descriptions-item label="行数差">{{ cmpDlg.report.row_diff }}</el-descriptions-item>
        </el-descriptions>
        <el-alert
          :type="cmpDlg.report.consistent ? 'success' : 'warning'" show-icon :closable="false"
          :title="cmpDlg.report.consistent ? '源目一致' : '存在差异'"
          :description="`抽样 ${cmpDlg.report.sample.rows} 行：仅源有 ${cmpDlg.report.sample.only_in_source}，仅目标有 ${cmpDlg.report.sample.only_in_target}`"
          style="margin: 12px 0"
        />
        <template v-if="cmpDlg.report.sample.src_examples.length">
          <div class="cc-sub" style="display: flex; align-items: center; gap: 8px">
            仅源有的样例（前5）
            <el-button link type="primary" size="small" icon="CopyDocument"
                       @click="copyText(JSON.stringify(cmpDlg.report.sample.src_examples), '样例')">复制</el-button>
          </div>
          <pre class="sql-pre" style="max-height: 120px">{{ JSON.stringify(cmpDlg.report.sample.src_examples) }}</pre>
        </template>
        <template v-if="cmpDlg.report.sample.dst_examples.length">
          <div class="cc-sub">仅目标有的样例（前5）</div>
          <pre class="sql-pre" style="max-height: 120px">{{ JSON.stringify(cmpDlg.report.sample.dst_examples) }}</pre>
        </template>
      </template>
      <div v-else v-loading="true" style="height: 200px" />
      <template #footer>
        <el-button @click="cmpDlg.visible = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 文件导入（CSV/Excel/TXT/JSON 入仓） -->
    <el-dialog v-model="impDlg.visible" title="文件导入" width="560px" destroy-on-close @closed="resetImport">
      <el-upload
        drag :auto-upload="false" :limit="1" :on-change="onImpPick" :file-list="impDlg.files"
        accept=".csv,.txt,.json,.xlsx,.xls"
      >
        <el-icon size="42" color="#98a2b3"><UploadFilled /></el-icon>
        <div class="el-upload__text">拖拽文件到此处，或点击选择</div>
        <template #tip>
          <div class="muted tiny">支持 CSV / TXT / JSON（对象数组）/ Excel(.xlsx) · ≤20MB · 首行为表头</div>
        </template>
      </el-upload>

      <el-form label-width="100px" style="margin-top: 12px">
        <el-form-item label="目标数据源" required>
          <el-select v-model="impForm.datasource_id" filterable style="width: 100%">
            <el-option v-for="d in dsListAll" :key="d.id" :label="`${d.name} (${d.type})`" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="目标表名" required>
          <el-input v-model="impForm.table_name" class="mono" placeholder="默认取文件名（可改）" />
        </el-form-item>
        <el-form-item label="冲突策略">
          <el-radio-group v-model="impForm.if_exists">
            <el-radio value="append">追加</el-radio>
            <el-radio value="replace">替换（重建表）</el-radio>
            <el-radio value="fail">已存在则报错</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>

      <div v-if="impDlg.result" class="page-card" style="margin-top: 6px">
        <div style="font-weight: 600; margin-bottom: 4px">
          导入{{ impDlg.result.error ? '失败' : '成功' }}：{{ impDlg.result.table || '' }}
        </div>
        <div v-if="!impDlg.result.error" class="mono tiny" style="color: #67c23a">
          ✅ {{ impDlg.result.rows }} 行 → {{ impDlg.result.target }}.{{ impDlg.result.table }}（{{ impDlg.result.duration_ms }} ms）
        </div>
        <div v-else class="mono tiny" style="color: #f56c6c">{{ impDlg.result.error }}</div>
      </div>

      <template #footer>
        <el-button @click="impDlg.visible = false">关闭</el-button>
        <el-button type="primary" :loading="impDlg.saving"
                   :disabled="!impDlg.file || !impForm.datasource_id" @click="runImport">
          开始导入
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import http, { errMsg } from '../api'
import { openAi } from '../ai'
import { useAuthStore } from '../store'

const route = useRoute()
const auth = useAuthStore()

/** analyst 角色为只读模式（菜单名"数据查询"） */
const readonly = computed(() => auth.role === 'analyst')
const pageTitle = computed(() => (readonly.value ? '数据查询' : '数据集成'))

const list = ref([])
const loading = ref(false)
const statusFilter = ref('')

// ---- 整库同步 / 数据比对 ----
const dsListAll = ref([])
const bulkTables = ref([])
const bulkDlg = reactive({ visible: false, saving: false })
const bulkForm = reactive({ source_datasource_id: null, target_datasource_id: null,
                            tables: [], write_mode: 'append', cron: '', publish: true })
const bulkResult = ref(null)
const cmpDlg = reactive({ visible: false, jobName: '', report: null })

async function openBulk() {
  if (!dsListAll.value.length) {
    try { dsListAll.value = await http.get('/api/datasources') } catch { /* ignore */ }
  }
  bulkDlg.visible = true
}

// ---- 文件导入 ----
const impDlg = reactive({ visible: false, saving: false, file: null, files: [], result: null })
const impForm = reactive({ datasource_id: null, table_name: '', if_exists: 'append' })

function openImport() {
  impDlg.visible = true
  if (!dsListAll.value.length) {
    http.get('/api/datasources').then((d) => { dsListAll.value = d }).catch(() => {})
  }
}

function onImpPick(file) {
  impDlg.file = file.raw
  if (!impForm.table_name) {
    impForm.table_name = (file.name || '').replace(/\.[^.]+$/, '').replace(/[^a-zA-Z0-9_\u4e00-\u9fa5]/g, '_')
  }
}

function resetImport() {
  impDlg.file = null
  impDlg.files = []
  impDlg.result = null
}

async function runImport() {
  if (!impDlg.file || !impForm.datasource_id) return
  if (!impForm.table_name.trim()) { ElMessage.warning('请填写目标表名'); return }
  impDlg.saving = true
  impDlg.result = null
  try {
    const fd = new FormData()
    fd.append('file', impDlg.file)
    fd.append('target_datasource_id', impForm.datasource_id)
    fd.append('table_name', impForm.table_name.trim())
    fd.append('if_exists', impForm.if_exists)
    impDlg.result = await http.post('/api/import/run', fd)
    ElMessage.success(`导入成功：${impDlg.result.rows} 行`)
    load()
  } catch (e) {
    impDlg.result = { error: errMsg(e) }
  } finally {
    impDlg.saving = false
  }
}

function copyText(text, what) {
  navigator.clipboard?.writeText(text)
  ElMessage.success(what + ' 已复制')
}

async function loadBulkTables() {
  bulkTables.value = []
  bulkForm.tables = []
  if (!bulkForm.source_datasource_id) return
  try {
    const d = await http.get('/api/meta/tables', {
      params: { datasource_id: bulkForm.source_datasource_id, size: 500 } })
    bulkTables.value = (d.items || []).map((t) => t.name)
  } catch { /* ignore */ }
}

async function runBulk() {
  bulkDlg.saving = true
  bulkResult.value = null
  try {
    bulkResult.value = await http.post('/api/integ/jobs/bulk', {
      source_datasource_id: bulkForm.source_datasource_id,
      target_datasource_id: bulkForm.target_datasource_id,
      tables: bulkForm.tables, write_mode: bulkForm.write_mode,
      cron: bulkForm.cron, publish: bulkForm.publish })
    ElMessage.success(`完成：新建 ${bulkResult.value.created.length} 个任务`)
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    bulkDlg.saving = false
  }
}

async function compareJob(row) {
  cmpDlg.jobName = row.name
  cmpDlg.report = null
  cmpDlg.visible = true
  try {
    cmpDlg.report = await http.post(`/api/integ/jobs/${row.id}/compare`, {})
  } catch (e) {
    cmpDlg.visible = false
    ElMessage.error(errMsg(e))
  }
}

// ---- 类型映射规则 ----
const mapDlg = reactive({ visible: false })
const mapLoading = ref(false)
const mappings = ref([])
const mapForm = reactive({
  visible: false, saving: false,
  source_engine: '*', target_engine: '*', source_dtype: '', target_dtype: '', note: '',
})

async function openMappings() {
  mapDlg.visible = true
  await loadMappings()
}

async function loadMappings() {
  mapLoading.value = true
  try {
    mappings.value = await http.get('/api/integ/typemappings')
  } catch { /* ignore */ } finally {
    mapLoading.value = false
  }
}

function openMapForm() {
  Object.assign(mapForm, { source_engine: '*', target_engine: '*',
                           source_dtype: '', target_dtype: '', note: '', saving: false })
  mapForm.visible = true
}

async function saveMapping() {
  if (!mapForm.source_dtype.trim() || !mapForm.target_dtype.trim()) {
    ElMessage.warning('源类型与目标类型必填')
    return
  }
  mapForm.saving = true
  try {
    await http.post('/api/integ/typemappings', {
      source_engine: mapForm.source_engine, target_engine: mapForm.target_engine,
      source_dtype: mapForm.source_dtype, target_dtype: mapForm.target_dtype,
      note: mapForm.note,
    })
    ElMessage.success('已保存')
    mapForm.visible = false
    loadMappings()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    mapForm.saving = false
  }
}

async function removeMapping(row) {
  try {
    await ElMessageBox.confirm(
      `删除映射 ${row.source_dtype} → ${row.target_dtype}（${row.target_engine}）？`,
      '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.delete(`/api/integ/typemappings/${row.id}`)
    ElMessage.success('已删除')
    loadMappings()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}
const dsList = ref([])
const workflows = ref([])
const saving = ref(false)

const dlg = reactive({ visible: false, isEdit: false, id: null })
const form = reactive({
  name: '', source_datasource_id: null, source_table: '', source_query: '',
  target_datasource_id: null, target_table: '', write_mode: 'append',
  increment_enabled: false, increment_column: '', pre_sql: '', post_sql: '',
})

const valDlg = reactive({ visible: false, loading: false, ok: true, issues: [], ddl: '' })
const trialDlg = reactive({ visible: false, loading: false, stats: null, columns: [], rows: [] })
const pubDlg = reactive({ visible: false, id: null, cron: '0 2 * * *', depends_on: [], saving: false })
const verDlg = reactive({ visible: false, job: null, versions: [], loading: false, datax: '', dataxVersion: null })

const trialRows = computed(() => (trialDlg.rows || []).map((r) => Object.fromEntries((r || []).map((v, i) => [`c${i}`, v]))))

function dsTag(t) {
  return { mysql: 'primary', doris: 'warning', postgres: 'success', sqlite: 'info' }[t] || 'info'
}
function statusTag(s) {
  return { draft: 'info', published: 'success', offline: 'warning' }[s] || 'info'
}
function statusLabel(s) {
  return { draft: '草稿', published: '已发布', offline: '已下线' }[s] || s
}
function writeModeLabel(m) {
  return { append: 'append 追加', truncate_insert: 'truncate 插入', replace: 'replace 替换' }[m] || m || '-'
}

async function load() {
  loading.value = true
  try {
    list.value = await http.get('/api/integ/jobs', { params: { status: statusFilter.value || '' } })
  } catch { /* ignore */ } finally {
    loading.value = false
  }
}

async function loadRefs() {
  try {
    dsList.value = await http.get('/api/datasources')
  } catch { /* ignore */ }
  try {
    workflows.value = await http.get('/api/sched/workflows')
  } catch { /* ignore */ }
}

function resetForm() {
  Object.assign(form, {
    name: '', source_datasource_id: null, source_table: '', source_query: '',
    target_datasource_id: null, target_table: '', write_mode: 'append',
    increment_enabled: false, increment_column: '', pre_sql: '', post_sql: '',
  })
}

function openCreate() {
  dlg.isEdit = false
  dlg.id = null
  resetForm()
  dlg.visible = true
}

function openEdit(row) {
  dlg.isEdit = true
  dlg.id = row.id
  Object.assign(form, {
    name: row.name,
    source_datasource_id: row.source?.id || null,
    source_table: row.source?.table || '',
    source_query: row.source?.query || '',
    target_datasource_id: row.target?.id || null,
    target_table: row.target?.table || '',
    write_mode: row.config?.write_mode || 'append',
    increment_enabled: !!row.config?.increment?.enabled,
    increment_column: row.config?.increment?.column || '',
    pre_sql: row.config?.pre_sql || '',
    post_sql: row.config?.post_sql || '',
  })
  dlg.visible = true
}

function buildPayload() {
  if (!form.name.trim() || !form.source_datasource_id || !form.target_datasource_id || !form.target_table.trim()) {
    ElMessage.warning('请完整填写任务名称、源/目标数据源与目标表')
    return null
  }
  return {
    name: form.name.trim(),
    source_datasource_id: form.source_datasource_id,
    source_table: form.source_table.trim(),
    source_query: form.source_query.trim(),
    target_datasource_id: form.target_datasource_id,
    target_table: form.target_table.trim(),
    config: {
      write_mode: form.write_mode,
      increment: { enabled: form.increment_enabled, column: form.increment_column.trim() },
      pre_sql: form.pre_sql.trim(),
      post_sql: form.post_sql.trim(),
    },
  }
}

async function save() {
  const payload = buildPayload()
  if (!payload) return
  saving.value = true
  try {
    if (dlg.isEdit) {
      await http.put(`/api/integ/jobs/${dlg.id}`, payload)
      ElMessage.success('已保存')
    } else {
      await http.post('/api/integ/jobs', payload)
      ElMessage.success('任务已创建（草稿）')
    }
    dlg.visible = false
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    saving.value = false
  }
}

async function validate(row) {
  valDlg.visible = true
  valDlg.loading = true
  valDlg.issues = []
  valDlg.ddl = ''
  try {
    const res = await http.post(`/api/integ/jobs/${row.id}/validate`)
    valDlg.ok = res.ok
    valDlg.issues = res.issues || []
    valDlg.ddl = res.ddl || ''
  } catch (e) {
    valDlg.ok = false
    valDlg.issues = [{ level: 'error', message: errMsg(e) }]
  } finally {
    valDlg.loading = false
  }
}

async function trial(row) {
  trialDlg.visible = true
  trialDlg.loading = true
  trialDlg.stats = null
  trialDlg.columns = []
  trialDlg.rows = []
  try {
    const res = await http.post(`/api/integ/jobs/${row.id}/trial`)
    trialDlg.stats = res.stats
    trialDlg.columns = res.columns || []
    trialDlg.rows = res.rows || []
  } catch (e) {
    ElMessage.error(errMsg(e))
    trialDlg.visible = false
  } finally {
    trialDlg.loading = false
  }
}

function openPublish(row) {
  pubDlg.id = row.id
  pubDlg.cron = '0 2 * * *'
  pubDlg.depends_on = []
  pubDlg.visible = true
}

async function doPublish() {
  pubDlg.saving = true
  try {
    const res = await http.post(`/api/integ/jobs/${pubDlg.id}/publish`, {
      cron: pubDlg.cron.trim(),
      depends_on: pubDlg.depends_on,
    })
    pubDlg.visible = false
    ElMessageBox.alert(
      `已发布 v${res.version}，生成工作流「${res.workflow}」(#${res.workflow_id})，可在调度运维查看实例。`,
      '发布成功', { type: 'success' }
    ).catch(() => {})
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  } finally {
    pubDlg.saving = false
  }
}

async function offline(row) {
  try {
    await ElMessageBox.confirm(`下线任务「${row.name}」？调度将被移除。`, '确认', { type: 'warning' })
  } catch { return }
  try {
    await http.post(`/api/integ/jobs/${row.id}/offline`)
    ElMessage.success('已下线')
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function removeJob(row) {
  try {
    await ElMessageBox.confirm(
      `删除任务「${row.name}」？将连同版本历史与血缘一并删除${row.status === 'offline' ? '，伴随工作流保留（有实例时）' : ''}。`,
      '删除确认', { type: 'warning', confirmButtonText: '删除' })
  } catch { return }
  try {
    await http.delete(`/api/integ/jobs/${row.id}`)
    ElMessage.success('已删除')
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function rollback(v) {
  try {
    await ElMessageBox.confirm(
      `将任务「${verDlg.job.name}」配置回滚到 v${v.version}？回滚后需重新发布生效。`,
      '回滚确认', { type: 'warning' })
  } catch { return }
  try {
    await http.post(`/api/integ/jobs/${verDlg.job.id}/versions/${v.id}/rollback`)
    ElMessage.success(`已回滚到 v${v.version}，请重新发布`)
    verDlg.visible = false
    load()
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

async function openVersions(row) {
  verDlg.job = row
  verDlg.versions = []
  verDlg.datax = ''
  verDlg.visible = true
  verDlg.loading = true
  try {
    verDlg.versions = await http.get(`/api/integ/jobs/${row.id}/versions`)
  } catch { /* ignore */ } finally {
    verDlg.loading = false
  }
}

async function viewDatax(v) {
  try {
    const res = await http.get(`/api/integ/jobs/${verDlg.job.id}/versions/${v.id}/datax`)
    verDlg.datax = res.datax_json || JSON.stringify(res.config, null, 2) || '(空)'
    verDlg.dataxVersion = res.version
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onMounted(() => {
  load()
  loadRefs()
  http.get('/api/datasources').then((d) => { dsListAll.value = d }).catch(() => {})
})
</script>

<style scoped>
.col-head {
  margin: 0 0 10px 90px;
}
</style>
