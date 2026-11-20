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
        <el-checkbox v-if="!readonly" v-model="trialRealWrite" size="small">试跑真实写入</el-checkbox>
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
          <template #default="{ row }">
            <el-link type="primary" :underline="false" @click="openDetail(row)">
              <span style="font-weight: 600">{{ row.name }}</span>
            </el-link>
          </template>
        </el-table-column>
        <el-table-column label="源 → 目标" min-width="280">
          <template #default="{ row }">
            <span class="mono">
              <el-tag :type="dsTag(row.source?.type)" size="small" effect="plain">{{ row.source?.name || '?' }}</el-tag>
              {{ row.source?.table || row.source?.query || '?' }}
              <el-icon style="color: var(--dp-text-3); margin: 0 4px; vertical-align: -3px"><Right /></el-icon>
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
        <el-table-column label="最近运行" width="160">
          <template #default="{ row }">
            <span
              v-if="row.last_run" class="run-cell" :class="{ failed: row.last_run.status === 'failed' }"
              :title="row.last_run.error || ''"
              @click="row.workflow_id && $router.push(`/ops?workflow_id=${row.workflow_id}`)"
            >
              <span class="dp-dot" :class="runDot(row.last_run.status)" />{{ runLabel(row.last_run.status) }}
              <span class="muted tiny">{{ (row.last_run.finished_at || '').slice(5, 16) }}</span>
            </span>
            <span v-else class="muted">未运行</span>
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
            <el-form-item v-if="form.write_mode === 'append'" label="合并键">
              <el-input v-model="form.upsert_keys" placeholder="可选，目标列名逗号分隔，如：id, dt" />
              <div class="muted tiny">配置后同键数据合并而非追加（更新型表可增量）：MySQL=ON DUPLICATE KEY / PG=ON CONFLICT /
                Doris·StarRocks=UNIQUE KEY 模型 / 其他=先删后插兜底</div>
            </el-form-item>
          </el-col>
        </el-row>
        <el-divider style="margin: 4px 0 14px" />
        <el-row :gutter="16">
          <el-col :span="6">
            <el-form-item label="增量同步">
              <el-switch v-model="form.increment_enabled" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="增量列">
              <el-input v-model="form.increment_column" :disabled="!form.increment_enabled" placeholder="如：updated_at / id" />
            </el-form-item>
          </el-col>
          <el-col v-if="form.increment_enabled" :span="10">
            <el-form-item label="增量模式">
              <el-radio-group v-model="form.increment_mode">
                <el-radio-button value="max_value">断点续传</el-radio-button>
                <el-radio-button value="time_range">按表达式</el-radio-button>
              </el-radio-group>
            </el-form-item>
          </el-col>
        </el-row>
        <div v-if="form.increment_enabled" class="muted tiny" style="margin: -10px 0 10px 100px">
          <template v-if="form.increment_mode === 'max_value'">
            断点续传：首次自动全量，之后只同步「增量列 &gt; 目标当前最大值」的数据（断点存目标表，重置目标表可重新全量）
          </template>
          <template v-else>
            按表达式：每次只同步「增量列 ≥ 表达式解析值」的分区数据
          </template>
        </div>
        <el-row v-if="form.increment_enabled && form.increment_mode === 'time_range'" :gutter="16">
          <el-col :span="14">
            <el-form-item label="时间表达式">
              <el-input v-model="form.increment_expr" class="mono" placeholder="${bizdate} 或 ${bizdate_nodash}" />
            </el-form-item>
          </el-col>
          <el-col :span="10">
            <el-form-item label=" ">
              <el-button size="small" @click="resolveExpr">试算</el-button>
              <el-tag v-if="incResolved" size="small" effect="plain" class="mono" style="margin-left: 8px">
                {{ incResolved }}
              </el-tag>
            </el-form-item>
          </el-col>
        </el-row>
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="失败重试">
              <el-input-number v-model="form.retries" :min="0" :max="5" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="重试间隔">
              <el-input-number v-model="form.retry_delay_minutes" :min="1" :max="60" style="width: 100%" />
              <span class="muted tiny" style="margin-left: 8px">分钟</span>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <div class="muted tiny" style="padding-top: 30px">Airflow 模式下作为 DAG 重试策略下发；断点续传/清空重写均幂等，重试安全</div>
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
    <el-dialog v-model="trialDlg.visible" :title="`试跑结果（${trialDlg.dry ? '校验模式，未写目标' : '真实写入'} · 100 行）`" width="820px" top="6vh">
      <div v-if="trialDlg.loading" v-loading="true" style="height: 160px" />
      <template v-else>
        <el-alert
          v-if="trialDlg.dry" type="info" :closable="false" style="margin-bottom: 12px"
          title="校验模式：只读源库验证列映射与 SQL，不写入目标表（不影响增量断点）；勾选「试跑真实写入」可验证完整写入链路"
        />
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
      <div v-if="verDlg.job" class="toolbar" style="margin-bottom: 10px">
        <span class="muted tiny">目标：{{ verDlg.job.target?.name }}.{{ verDlg.job.target?.table }}</span>
        <div class="spacer" />
        <el-tooltip content="清空目标表数据（保留结构），下次同步重新全量、重建增量断点" placement="top">
          <el-button v-if="!readonly" size="small" type="danger" plain icon="RefreshLeft" @click="resetTarget(verDlg.job)">
            重置目标表
          </el-button>
        </el-tooltip>
      </div>
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

    <!-- 任务详情抽屉：基本信息 / 最近运行趋势 / 列映射 -->
    <el-drawer v-model="dtl.visible" :title="`任务详情：${dtl.job?.name || ''}`" size="760px">
      <template v-if="dtl.job">
        <el-descriptions :column="2" size="small" border style="margin-bottom: 14px">
          <el-descriptions-item label="状态">
            <el-tag :type="statusTag(dtl.job.status)" size="small">{{ statusLabel(dtl.job.status) }}</el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="版本">v{{ dtl.job.current_version }}</el-descriptions-item>
          <el-descriptions-item label="写入模式">{{ writeModeLabel(dtl.job.config?.write_mode) }}</el-descriptions-item>
          <el-descriptions-item label="合并键">
            <span class="mono">{{ (dtl.job.config?.upsert_keys || []).join(', ') || '-' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="增量">
            {{ dtl.job.config?.increment?.enabled
              ? `${dtl.job.config.increment.column}（${dtl.job.config.increment.mode || 'max_value'}）` : '关' }}
          </el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ dtl.job.updated_at }}</el-descriptions-item>
        </el-descriptions>

        <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px">
          <span style="font-weight: 600">最近运行（写入行数趋势）</span>
          <div class="spacer" />
          <el-button size="small" type="warning" plain icon="Bell" @click="createHaltRule(dtl.job)">建断流监控</el-button>
          <el-button
            v-if="dtl.job.workflow_id" size="small" type="primary" plain icon="Grid"
            @click="$router.push(`/ops?workflow_id=${dtl.job.workflow_id}`)"
          >运行矩阵</el-button>
        </div>
        <div v-show="dtl.trend.length" ref="dtlTrendEl" style="width: 100%; height: 180px; margin-bottom: 10px" />
        <el-empty v-if="!dtl.trend.length" description="暂无运行记录" :image-size="60" />
        <el-table v-else :data="dtl.trend" size="small" border>
          <el-table-column label="完成时间" width="165">
            <template #default="{ row }">{{ row.finished_at || '-' }}</template>
          </el-table-column>
          <el-table-column label="状态" width="95">
            <template #default="{ row }">
              <span class="dp-dot" :class="runDot(row.status)" /> {{ runLabel(row.status) }}
            </template>
          </el-table-column>
          <el-table-column label="触发" width="85">
            <template #default="{ row }">{{ row.trigger_type || '-' }}</template>
          </el-table-column>
          <el-table-column label="写入行数" width="100" align="right">
            <template #default="{ row }">{{ row.rows_written >= 0 ? row.rows_written : '-' }}</template>
          </el-table-column>
          <el-table-column label="耗时" width="90" align="right">
            <template #default="{ row }">{{ fmtDuration(row.duration_ms) }}</template>
          </el-table-column>
        </el-table>

        <div class="cc-sub" style="margin-top: 14px">列映射（{{ dtl.job.config?.columns?.length || 0 }}）</div>
        <el-table v-if="dtl.job.config?.columns?.length" :data="dtl.job.config.columns" size="small" border max-height="260">
          <el-table-column prop="source" label="源列" min-width="130" class-name="mono" />
          <el-table-column prop="target" label="目标列" min-width="130" class-name="mono" />
          <el-table-column label="源类型" width="130">
            <template #default="{ row }"><span class="mono muted">{{ row.source_type || '-' }}</span></template>
          </el-table-column>
          <el-table-column label="目标类型" width="130">
            <template #default="{ row }"><span class="mono muted">{{ row.target_type || '-' }}</span></template>
          </el-table-column>
        </el-table>
        <el-empty v-else description="尚未生成列映射（执行校验/试跑后自动读取）" :image-size="60" />
      </template>
      <div v-else v-loading="true" style="height: 300px" />
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
        <div v-for="c in bulkResult.created" :key="c.table" class="mono tiny" style="color: var(--dp-ok)">
          ✅ {{ c.table }} → 任务 #{{ c.job_id }}（v{{ c.version }}，{{ c.status }}）
        </div>
        <div v-for="c in bulkResult.skipped" :key="'s' + c.table" class="mono tiny" style="color: var(--dp-text-3)">
          ⏭ {{ c.table }}：{{ c.reason }}
        </div>
        <div v-for="f in bulkResult.failed" :key="'f' + f.table" class="mono tiny" style="color: var(--dp-danger)">
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
    <el-dialog v-model="cmpDlg.visible" :title="`数据比对：${cmpDlg.jobName}`" width="680px">
      <div class="toolbar" style="margin-bottom: 10px">
        <el-radio-group v-model="cmpDlg.mode" size="small" @change="runCompare">
          <el-radio-button value="sample">抽样比对</el-radio-button>
          <el-radio-button value="chunk">分块全量校验</el-radio-button>
        </el-radio-group>
        <span class="muted tiny">分块：按合并键/增量列切 10 段逐段计数（键列需数值型），定位差异区间</span>
      </div>
      <template v-if="cmpDlg.report">
        <el-descriptions :column="3" size="small" border>
          <el-descriptions-item label="源行数">{{ cmpDlg.report.source.rows }}</el-descriptions-item>
          <el-descriptions-item label="目标行数">{{ cmpDlg.report.target.rows }}</el-descriptions-item>
          <el-descriptions-item label="行数差">{{ cmpDlg.report.row_diff }}</el-descriptions-item>
        </el-descriptions>
        <el-alert
          v-if="cmpDlg.report.mode === 'chunk'" style="margin: 12px 0" :closable="false"
          :type="cmpDlg.report.chunk_diffs?.length ? 'error' : 'success'" show-icon
          :title="cmpDlg.report.chunk_diffs?.length
            ? `分块校验：${cmpDlg.report.chunks_ok}/${cmpDlg.report.chunks_total} 块一致，${cmpDlg.report.chunk_diffs.length} 块存在差异（键：${cmpDlg.report.chunk_key}）`
            : `分块校验：${cmpDlg.report.chunks_total} 块全部一致（键：${cmpDlg.report.chunk_key}）`"
        />
        <el-table v-if="cmpDlg.report.chunk_diffs?.length" :data="cmpDlg.report.chunk_diffs" size="small" border style="margin-bottom: 12px">
          <el-table-column prop="range" label="键区间" min-width="140" class-name="mono" />
          <el-table-column prop="source" label="源行数" width="100" align="right" />
          <el-table-column prop="target" label="目标行数" width="100" align="right" />
        </el-table>
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
        <el-icon size="42" color="var(--dp-text-3)"><UploadFilled /></el-icon>
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
        <div v-if="!impDlg.result.error" class="mono tiny" style="color: var(--dp-ok)">
          ✅ {{ impDlg.result.rows }} 行 → {{ impDlg.result.target }}.{{ impDlg.result.table }}（{{ impDlg.result.duration_ms }} ms）
        </div>
        <div v-else class="mono tiny" style="color: var(--dp-danger)">{{ impDlg.result.error }}</div>
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
import { computed, onBeforeUnmount, nextTick, onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import * as echarts from 'echarts'
import http, { errMsg, fmtDuration } from '../api'
import { openAi } from '../ai'
import { useAuthStore } from '../store'
import { dpAlpha, dpAxisLabel, dpColor, dpSplitLine, dpTooltip } from '../charts/theme'

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
const cmpDlg = reactive({ visible: false, jobName: '', jobId: null, report: null, mode: 'sample' })

async function compareJob(row) {
  cmpDlg.jobName = row.name
  cmpDlg.jobId = row.id
  cmpDlg.mode = 'sample'
  cmpDlg.report = null
  cmpDlg.visible = true
  await doCompare()
}

async function doCompare() {
  try {
    cmpDlg.report = await http.post(`/api/integ/jobs/${cmpDlg.jobId}/compare`, {},
                                    { params: { mode: cmpDlg.mode } })
  } catch (e) {
    cmpDlg.visible = false
    ElMessage.error(errMsg(e))
  }
}

/* ---------- 任务详情抽屉（基本信息 / 最近运行趋势 / 列映射） ---------- */
const dtl = reactive({ visible: false, job: null, trend: [], loading: false })
const dtlTrendEl = ref(null)
let dtlChart = null

async function openDetail(row) {
  dtl.job = row
  dtl.trend = []
  dtl.visible = true
  if (!row.workflow_id) return
  try {
    const res = await http.get(`/api/integ/jobs/${row.id}/trend`, { params: { limit: 12 } })
    dtl.trend = res.items || []
    nextTick(() => renderDtlTrend())
  } catch { /* ignore */ }
}

function renderDtlTrend() {
  if (!dtlTrendEl.value) return
  if (!dtlChart) dtlChart = echarts.init(dtlTrendEl.value)
  dtlChart.setOption({
    grid: { left: 56, right: 16, top: 22, bottom: 24 },
    tooltip: { ...dpTooltip(), trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: dtl.trend.map((t) => (t.finished_at || '').slice(5, 16)),
      axisLabel: dpAxisLabel(), axisLine: { lineStyle: { color: dpColor('border') } },
    },
    yAxis: { type: 'value', axisLabel: dpAxisLabel(), splitLine: dpSplitLine() },
    series: [{
      name: '写入行数', type: 'line',
      data: dtl.trend.map((t) => (t.rows_written >= 0 ? t.rows_written : null)),
      connectNulls: false, symbol: 'circle', symbolSize: 5,
      lineStyle: { width: 1.5 }, itemStyle: { color: dpColor('accent') },
      areaStyle: { color: dpAlpha('accent', 0.1) },
    }],
  }, true)
  dtlChart.resize()
}

async function createHaltRule(job) {
  try {
    await ElMessageBox.confirm(
      `创建同步断流监控：每日检查「${job.name}」最近一次成功实例的写入行数（阈值 1 行），` +
      '低于阈值即经告警通道通知。可在监控告警页调整阈值/调度。', '建断流监控',
      { type: 'info', confirmButtonText: '创建' })
  } catch { return }
  try {
    await http.post('/api/monitor/rules', {
      name: `${job.name.slice(0, 120)}-断流监控`, rule_type: 'sync_halt',
      datasource_id: 0, table_name: '', column_name: '',
      params: { workflow_name: job.name, min_rows: 1 },
      cron: '0 7 * * *', severity: 'warning', enabled: true, group_name: '', mute_hours: 24,
    })
    ElMessage.success('断流监控已创建（监控告警页可调整阈值与调度）')
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

onBeforeUnmount(() => {
  if (dtlChart) { dtlChart.dispose(); dtlChart = null }
})

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
  target_datasource_id: null, target_table: '', write_mode: 'append', upsert_keys: '',
  increment_enabled: false, increment_column: '', increment_mode: 'max_value',
  increment_expr: '', retries: 0, retry_delay_minutes: 5, pre_sql: '', post_sql: '',
})
const incResolved = ref('')

async function resolveExpr() {
  try {
    const res = await http.post('/api/integ/increment/resolve', null,
                               { params: { expr: form.increment_expr || '' } })
    incResolved.value = res.resolved || '(无占位符，按原文使用)'
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
}

const valDlg = reactive({ visible: false, loading: false, ok: true, issues: [], ddl: '' })
const trialDlg = reactive({ visible: false, loading: false, stats: null, columns: [], rows: [], dry: true })
const trialRealWrite = ref(false)
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
function runDot(s) {
  return { success: 'ok', failed: 'danger', running: 'run', waiting: 'idle' }[s] || 'idle'
}
function runLabel(s) {
  return { success: '成功', failed: '失败', running: '运行中', waiting: '等待' }[s] || (s || '-')
}
async function resetTarget(job) {
  try {
    await ElMessageBox.confirm(
      `清空目标表「${job.target?.table}」数据（保留表结构），下次同步将重新全量、重建增量断点。确定？`,
      '重置确认', { type: 'warning', confirmButtonText: '重置' })
  } catch { return }
  try {
    const res = await http.post(`/api/integ/jobs/${job.id}/reset_target`)
    ElMessage.success(`目标表已重置（${res.mode}），下次同步将全量执行`)
  } catch (e) {
    ElMessage.error(errMsg(e))
  }
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
    target_datasource_id: null, target_table: '', write_mode: 'append', upsert_keys: '',
    increment_enabled: false, increment_column: '', increment_mode: 'max_value',
    increment_expr: '', retries: 0, retry_delay_minutes: 5, pre_sql: '', post_sql: '',
  })
  incResolved.value = ''
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
    upsert_keys: (row.config?.upsert_keys || []).join(', '),
    increment_enabled: !!row.config?.increment?.enabled,
    increment_column: row.config?.increment?.column || '',
    increment_mode: row.config?.increment?.mode || 'max_value',
    increment_expr: row.config?.increment?.expr || '',
    retries: row.config?.retries ?? 0,
    retry_delay_minutes: row.config?.retry_delay_minutes ?? 5,
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
      upsert_keys: form.upsert_keys.split(/[,，\s]+/).map((s) => s.trim()).filter(Boolean),
      increment: {
        enabled: form.increment_enabled,
        column: form.increment_column.trim(),
        mode: form.increment_mode || 'max_value',
        expr: form.increment_mode === 'time_range' ? form.increment_expr.trim() : '',
      },
      retries: form.retries || 0,
      retry_delay_minutes: form.retry_delay_minutes || 5,
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
  if (trialRealWrite.value) {
    try {
      await ElMessageBox.confirm(
        '真实写入模式：样本数据会写入目标表（影响 max_value 增量断点）。确定继续？', '试跑确认',
        { type: 'warning', confirmButtonText: '真实写入' })
    } catch { return }
  }
  trialDlg.visible = true
  trialDlg.loading = true
  trialDlg.stats = null
  trialDlg.columns = []
  trialDlg.rows = []
  trialDlg.dry = !trialRealWrite.value
  try {
    const res = await http.post(`/api/integ/jobs/${row.id}/trial`, null,
                                { params: { dry_run: !trialRealWrite.value } })
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
.run-cell {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
  white-space: nowrap;
}
.run-cell.failed {
  color: var(--dp-danger);
}
.run-cell:hover {
  text-decoration: underline;
}
</style>
