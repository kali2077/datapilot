# DataPilot 交付验收报告

> 交付日期：2026-11-04 · 对照《08-开发计划与里程碑》M1-M4 验收脚本
> 代码：`/Users/mac/Desktop/data-platform/` · 设计文档：`/Users/mac/Desktop/大数据平台设计/`

## 一、总体结论

**M1-M4 全部交付。** 后端 87 个 API 端点、前端 12 个页面（Vue3 + Element Plus，构建产物由后端挂载）、31 个 pytest 用例（单元 + e2e）全绿并连跑三遍稳定；真机（uvicorn + 浏览器）完成全部四条里程碑验收演示脚本。

## 二、里程碑验收对照（计划 → 实测）

### M1 · 一句话建同步任务端到端（计划 W6）✅

| 验收脚本步骤 | 实测结果 |
|---|---|
| 登录 → 配置数据源 | ✅ 种子自带 demo_source/demo_warehouse；数据源页 CRUD+测试连接+采集（截图验收） |
| 对话："把 demo_source 的 orders 表每天凌晨2点同步到 demo_warehouse 的 ods_orders" | ✅ 规则引擎/LLM 识别意图，反问缺失信息 |
| AI 反问增量策略 → 确认 | ✅ 草稿 + 试跑 100 行（60 行/5ms/自动建表）+ 确认卡片（源/目标/写入模式/调度 键值展示 + DDL + 校验问题） |
| 点确认上线 | ✅ `publish_sync_job` 确认流 → 版本 v1 固化 + DataX JSON 渲染 + 工作流注册 cron `0 2 * * *` |
| Airflow/调度次日 02:00 自动执行 | ✅ LocalScheduler 注册成功（next_fire 计算），手动触发实例 `success`，数据落库验证 50 行 |

### M2 · ETL 编排 + 问数 + 运维对话化（计划 W10）✅

| 验收脚本步骤 | 实测结果 |
|---|---|
| 对话："基于 ods_orders 做 dwd 订单明细，剔除测试账号，每天跑" | ✅ SQL 生成（含 TODO 过滤注释）→ sqlglot 校验 → EXPLAIN → 血缘查上游自动挂依赖 → 确认上线 |
| 次日按依赖顺序执行 | ✅ 跨工作流依赖（waiting + 轮询）+ SQL 节点 CTAS 自动建表 → 实例 success |
| 业务人员问"上月 GMV 多少"返回数字+图 | ✅ 中文问数命中元数据注释 → SQL → 表格 + 图表提示（echarts 柱图）；术语表/GMV 定义注入 |
| 失败告警 | ✅ 失败实例 → 邮件/webhook 通道触发 → 告警记录落库（测试覆盖） |

### M3 · 资产/地图/血缘 + 数据服务（计划 W14）✅

| 验收脚本步骤 | 实测结果 |
|---|---|
| 地图搜"订单"找到表 → 查看血缘上游 | ✅ 倒排索引搜索命中（含中文 2-gram）；表详情：字段/样例/上下游/相关工作流；SVG 血缘图（中心蓝/上游绿/下游橙/跳数切换）截图验收 |
| 对话发布"近30天下单用户数"为 API | ✅ `publish_api` 确认流 → 发布；发布/试执行/文档齐全 |
| 生成 AK/SK → curl 调用成功 | ✅ HMAC-SHA256 签名调用返回 20 行；错签名 401 / 未授权 403 / 超限流 403 / 时间窗校验（测试覆盖） |
| 审计页可见调用记录 | ✅ ApiLog 落库（含耗时/行数/状态）；平台审计三来源 ui/api/ai |

### M4 · GA（计划 W16）✅

- 权限矩阵：4 角色 × 写操作/管理操作 403 断言（测试覆盖）；analyst 不能建任务、viewer 只读、admin 审计
- 部署工件：`deploy/docker-compose.yml`（MySQL+Doris FE/BE+Airflow+DataX+app+nginx）、`deploy/airflow/factory_dag.py`（配置驱动动态 DAG，语法校验通过）、`nginx.conf`、`backup.sh`（sqlite+mysql 双路径+保留策略）、`Dockerfile`
- 引擎可插拔验证：SCHEDULER_ENGINE=airflow 配置文件渲染 + REST 启停代码路径；DATAX_HOME 下 DataX JSON 渲染正确（mysqlreader/doriswriter/rdbms 族）
- 备份/重置脚本可执行（chmod +x）
- 全量回归：pytest 31/31，连续三遍

## 二·五、补充交付：数据查询工作台（2026-11-04 补齐）

PRD 模块清单中的"数据查询"此前能力被拆分在对话查数（query_data）与工作流 SQL 编辑器中，缺少独立工作台页面。已补齐：

- **页面**（`web/src/views/Query.vue`，菜单"数据管理→数据查询"，全部角色可见）：
  数据源/表清单点击填 SQL、只读编辑器（Ctrl/Cmd+Enter 运行）、结果分页表格、
  实际执行 SQL 面板、CSV 导出、查询历史（点击回填）、保存查询（个人）
- **后端**（`app/routers/query.py`）：`/api/query/run|history|saved` —— sqlguard 只读白名单、
  自动 LIMIT、超时熔断、每用户历史 200 条、审计 query.run
- **测试**：新增 5 用例（只读执行/DML 拦截/历史/保存隔离/审计），全量 **36/36 passed**
- 浏览器实测：点表填 SQL → 运行 → 20 行/2ms 结果渲染 ✅（截图验收）

## 二·七、补充交付：数据监控告警模块（2026-11-04 第三轮）

在调度告警（任务失败通知）之上补齐**数据层监控**：

- **7 类监控规则**（`app/services/monitor_service.py`）：行数波动（基线自动建立、±pct%）、行数阈值、
  空值率、唯一性（重复值）、数据延迟（freshness）、自定义只读 SQL 校验（返回行>0 即告警）、任务时长
- **调度**：每规则独立 cron（复用 LocalScheduler，airflow 模式下也由平台执行）；启停/删除同步生效
- **告警联动**：检查失败 → 复用告警通道（email/webhook）→ AlertRecord（`[DataPilot] 监控告警[severity]`），
  与任务失败告警同一套通道与记录页
- **页面**（`/monitoring` 数据监控告警）：概览 6 卡片（规则/启用/24h检查/失败/通过率/告警数）+
  异常规则红色横幅 + 规则管理（按类型动态参数表单：表/列下拉来自元数据）+ 检查记录（筛选/分页）
- **测试**：新增 5 用例（7 类规则全走通 + 告警联动落库 + 概览 + 权限/校验），全量 **45/45 passed**
- 浏览器实测：波动规则基线建立（通过）+ 阈值规则失败（横幅+状态标）+ 概览实时 ✅（截图验收）

## 二·八、监控模块扩展（2026-11-04 第四轮）

在数据监控告警模块上追加三项能力（新增 2 测试，全量 **47/47 passed**）：

1. **告警静默窗口**：规则级 `mute_hours`（默认 24h，0=每次都通知）——静默期内失败检查照常落库但
   不重复通知（检查记录 message 标注"静默期内"）；告警通道配置轮换后仍受静默约束
2. **检查结果趋势图**：`GET /api/monitor/trend` 按天聚合 通过/失败/异常，前端 echarts 堆叠柱状图
   （近 7 天，跟随规则筛选联动）
3. **规则分组**：`group_name` 字段 + 规则表分组列 + 工具栏分组筛选下拉
4. **轻量迁移机制**：`db.run_migrations()`（create_all 不改已有表结构，启动时按 MIGRATIONS
   清单 ALTER ADD COLUMN 补齐增量列）——存量 mon_rule 自动获得新字段，老规则兼容

浏览器验收：趋势图渲染（绿/红堆叠+图例）、编辑对话框静默/分组字段、分组筛选 ✅（截图）

## 二·九、全模块 Bug 审查轮（2026-11-04 第五轮，目标模式）

逐模块逻辑复查 + pyflakes，发现并修复 7 处（新增 6 测试，全量 **53/53 passed**）：

| # | 严重度 | 问题 | 修复 |
|---|---|---|---|
| 1 | **高** | 删除数据源遗留孤儿元数据/标签/血缘（资产搜索出已删表、搜索索引脏） | 级联清理 MetaTable/MetaColumn/TableTag/LineageEdge + 索引失效 |
| 2 | **高** | 删除被引用的数据源（同步任务/API/监控规则/保存查询）静默成功 → 下游任务全挂 | 引用计数守卫，400 列出引用明细 |
| 3 | 中 | 同步任务/工作流/数据源/API **改名撞唯一约束 → 500** | 四处更新路径补重名校验 → 400 |
| 4 | 中 | Agent `_extract_params` 把 `::` 类型转换误报为绑定参数（如 `NOW()::date` → 虚假 :date 参数，Doris/PG 场景发布 API 必错） | 正则加 `(?<!:)` 负向断言 |
| 5 | 中 | `check_readonly` 放行多语句（`SELECT 1; SELECT 2`），驱动层行为不可控 | 强制单条语句 |
| 6 | 低 | 签名比较用 `!=`（非常量时间） | `hmac.compare_digest` |
| 7 | 低 | ①表详情"相关工作流"只查 sync 工作流，漏 ETL 工作流 ②MonitorCheck 无限增长 | ①改由血缘边反查（sync+sql 全覆盖）②每规则滚动保留 500 条 |

pyflakes 全仓 0 逻辑告警；前端确认：工作流编辑 PUT 不含 nodes（节点不可被意外清空）、对话/查询工作台输入链路正常。

## 二·十、AI 横切层贯通（2026-11-04 第六轮）

回应"AI 对话体现在哪里？除了智能问数其他的也要的"——AI 从单一页面升级为平台级横切层：

- **全局 AI 抽屉**：右下角悬浮球（任何页面可用）→ 侧边抽屉（会话管理/消息流/ChatCard 卡片复用/
  确认流完整），发送时自动携带 `page` 上下文
- **页面上下文注入**：Agent system prompt 感知用户当前页面（9 个页面映射提示），LLM 模式下 AI
  主动引导对应工具；审计记录也带 page 字段
- **3 个横切工具**（工具总数 9→13）：`get_instance_detail`（实例诊断+失败原因提示）、
  `monitor_overview`（监控概览+异常规则）、`run_monitor_check`（触发检查，engineer 权限）
- **各模块快捷入口**：集成页"AI 建同步" / 工作流页"AI 建 ETL" / 调度运维实例抽屉"AI 诊断" /
  资产表详情"问 AI"（全角色）/ 监控页"AI 查监控"
- **测试**：新增 4 用例（page 上下文/实例诊断卡/监控概览+触发检查/analyst 权限拒绝），全量 **57/57**
- 浏览器实测：悬浮球 → 抽屉查数（表格卡）→ 建同步（试跑+校验+DDL 确认卡）→ 确认 → 上线消息+跳转按钮 ✅

## 二·十一、AI 助手页面化 + 多标签页导航（2026-11-04 第七轮）

按用户反馈改造 AI 交互形态：

1. **AI 助手页面化**（不再用抽屉）：新增 `/ai-assistant` 路由页（左侧导航"智能应用→AI 助手"，
   全角色），右侧内容区全页对话——会话侧栏 + 消息流 + 全卡片 + 指令示范 + 模块快捷入口预填
   （各模块 `openAi(prefill)` 改为带 `?prefill=` 跳转）
2. **多标签页导航**：平台级标签条——点任何菜单在顶部开一个标签，可同时打开多个页面、
   点击切换、X 关闭（智能问数为固定首页标签不可关；表详情类路由按 tabKey 合并去重）
3. **指令示范（上下文感知）**：`PromptChips` 组件按当前页面动态推荐可点击示例指令
   （集成页推建同步 / 运维页推诊断·暂停 / 监控页推检查 / AI 页推全能力…），
   AiDrawer 与智能问数页、AI 助手页共用
4. **清理**：移除 AiDrawer/悬浮球/侧边栏抽屉入口及样式；规则引擎新增
   「介绍一下表 X」(get_table_schema) 与「你能做什么」(帮助) 两个指令分支
5. **测试**：新增 1 用例（介绍表+帮助），全量 **58/58 passed**
6. 浏览器实测：4 页多标签共存与切换、关闭标签、AI 页 chips 点击填入、侧边栏入口高亮 ✅（截图）

## 二·十二、同义词进检索链（②，2026-11-04 第八轮）

问数 RAG 升级：**术语族展开**——纯代码实现、零部署依赖（不需要 embedding 模型）：

- `build_context` / 规则引擎 `_match_table`：问题词元命中任一**术语族**（术语+同义词+定义）→
  整族词元并入检索（如问「流水」→ 命中 GMV 族「成交总额/流水/已支付订单金额」→ 召回 orders）
- system prompt 新增「命中术语(务必遵循其口径)」行，LLM 模式下同样生效
- 规则引擎查数分支放宽：查询动词开头 + 能召回表 → 直接查（此前被关键词门槛挡住）
- 测试：新增 1 用例（「统计流水Top10」召回 orders + 命中术语断言），全量 **59/59 passed**
- 真机验证：「查一下流水」→ 出 orders 数据 ✅（改造前：兜底帮助文案）

> ①向量检索按既定策略暂缓：做成"配 API key 即启用语义检索、不配自动降级词元匹配"的可插拔形态，
> 部署永远不被 embedding 模型绑架。

## 二·十三、数据源扩展：Oracle / StarRocks / ClickHouse（2026-11-04 第九轮）

数据源类型从 4 种扩展到 **7 种**（新增 9 测试，全量 **68/68 passed**）：

| 类型 | 接入方式 | 平台内支持点 |
|---|---|---|
| **Oracle** | oracledb thin 模式（**无需装 Oracle 客户端**），service_name=database 字段，1521 | 方言 oracle（sqlglot）、VARCHAR2/NUMBER/CLOB 类型映射、`_q` 双引号、JDBC thin 渲染 |
| **StarRocks** | MySQL 协议 9030（与 Doris 同族） | 类型映射**复用 doris 知识库**、TINYINT/STRING 族、mysql JDBC |
| **ClickHouse** | clickhouse-sqlalchemy HTTP 8123 | 方言 clickhouse（sqlglot）、Int64/String/DateTime 映射、**自动建表/CTAS 自动带 ENGINE=MergeTree** |

统一覆盖：连接引擎、SQL 护栏方言、类型映射知识库（+归一化）、DataX reader/writer JDBC+驱动渲染、
监控标识符引用、前端类型选择器（7 种+默认端口联动+标签色）。依赖新增 `oracledb`、`clickhouse-sqlalchemy`
（均为纯 Python，pip 即装）。

## 二·十四、数据源扩展二期 P0+P1+P2 全量交付（2026-11-05）

按 10 号规划文档完成**全部三期**，数据源类型 7 → **11 种**（新增 11 测试，全量 **79/79 passed**）：

### P0 · Trino / Hive / Spark 直连
- URL：`trino://user@host:8080/catalog/schema`、`hive://u:p@h:10000/db?auth=NONE`（Spark 复用 hive 方言，10001）
- 方言：sqlglot hive/spark/trino（只读护栏/LIMIT 均按方言）
- 类型映射：hive/spark STRING 族、trino VARCHAR/JSON 族（**修复 dict 字面量重复键覆盖 bug**：
  扩展循环里 hive 族与 trino 族同键互相覆盖导致映射丢失，重构为两个独立循环）
- DataX：trino-jdbc / hive2 JDBC + 驱动；**修复 render 密码用 Fernet 密文的潜在 bug**（统一 `_pwd` 解密）
- 建表方言：hive/spark `STORED AS PARQUET`、trino 双引号、maxcompute `LIFECYCLE`

### P1 · MaxCompute（pyodps 专用通道）
- `mc_client.py`：连接测试/只读查询(tunnel)/写入/表清单/schema/行读取/批量 INSERT/建表
- 引擎分派：run_query/execute_write/table_names/test_connection 按 type 走 pyodps；
  **修复 DataX 渲染前述密码 bug 在 MC 场景的暴露面**（odpsreader/odpswriter 用解密 AK/SK）
- 同步四向组合（MC↔任意源）：读端 SQL 增量+LIMIT、写端 append（truncate/replace 明确报错提示）、
  双端自动建表；元数据采集专用分支（list_tables + schema + count）
- validate/试跑/目标表检查全部分流（MC 无 SQLAlchemy 引擎）

### P2 · Hadoop 平台配置页
- 集群注册（HDFS WebHDFS/HiveServer2/Metastore/Spark/Trino 五端点 + Trino catalog）
- Kerberos（realm/principal + keytab 上传，Fernet 目录存储）；组件级连通测试（WebHDFS HTTP 探活/TCP）
- **一键派生**：hive/spark/trino 数据源（预填 host/port/auth=KERBEROS/NONE + cluster_id 引用），
  有派生引用时平台删除被守卫
- 前端：数据管理 → "Hadoop 平台"页（/hadoop）；数据源类型选择器升级 11 种下拉 + 端口联动 + MC endpoint 字段

### 测试与真机
- 新增 11 用例（三源 URL/方言/映射/JDBC/渲染/DDL + MC 通道/渲染 + 平台生命周期/权限），全量 **79/79 两遍稳定**
- 真机：平台注册 → 组件连通（优雅失败）→ Kerberos 派生 hive 数据源 → 列表可见 ✅（截图）
- 部署依赖新增（均纯 Python）：`trino[sqlalchemy]`、`pyhive`+`thrift`+`thrift-sasl`（auth=NONE 不需 sasl）、`pyodps`

## 二·十五、展示层调整（2026-11-05）

- **类型映射规则**从主导航撤下（原"类型映射知识库"，与问数 RAG 的术语/样例"知识库"撞名且低频），
  迁入**数据集成页工具栏**——对话框内管理（admin 可编辑、engineer 只读），贴近使用现场
  （试跑发现映射不对，当场补规则当场重跑）
- 系统管理组因此变为：审计日志 / 用户管理（/users）两个菜单，/kb 路由移除，System.vue 简化为纯用户管理页
- 后端零改动（typemappings API 与 map_dtype 优先级不变：KB → GENERIC_MAP）

## 二·十六、Hadoop 平台并入数据源管理（2026-11-05）

- 撤下"Hadoop 平台"独立菜单/路由（/hadoop），整体并入**数据源管理页**为页内双 tab：
  「数据源」「Hadoop 平台」——集群注册/连通测试/派生/Kerberos keytab 全部功能保留
- 多标签导航下不再多占一个标签位，数据相关操作收拢在同一页面

## 二·十七、问数知识库独立成页 + 子导航（2026-11-05）

对齐 SQLBot 的知识库管理形态：

- **侧边栏子导航**：「智能问数」改为可展开父菜单——问数对话 / SQL 示例 / 术语库
  （el-sub-menu + 当前路由自动展开父级；多标签页三页可并存）
- **SQL 示例独立页**（/fewshots）：问题→SQL CRUD、关联数据源、启用开关（原元数据管理 tab 迁出）
- **术语库独立页**（/glossary）：术语/同义词/口径定义 CRUD（迁出），页头说明其 RAG 作用
- 元数据管理页瘦身为纯表/字段管理
- 后端零改动（glossary/fewshot API 复用）；**79/79 回归通过**
- RAG 说明：术语族展开（②）+ few-shot 注入已生效；embedding 语义召回按 09 号方案待触发

## 二·十八、T1+T2 增强：文件入仓/脱敏/整库同步/数据比对/主机监控/SQL Server/BI 入口（2026-11-05）

按 11 号评估路线完成 T1+T2 全部 7 项（新增 7 测试，全量 **88/88 passed**）：

### T1
1. **文件入仓**（`POST /api/import/run`）：CSV/Excel(openpyxl)/TXT/JSON 上传 → 表头规范化+去重 →
   类型推断（数值/浮点/布尔/日期/文本采样判定）→ 按目标引擎建表（CH ENGINE/Mysql charset）→
   批量导入；append/replace/fail 语义；审计留痕。真机：中文 CSV 2 行导入+查询 ✅
2. **列级数据脱敏**（`/api/mask/rules` CRUD）：phone/id_card/email/custom 正则四策略，
   作用域=全部/指定数据源；**四处出参统一套用**（查询工作台/智能问数/数据服务 API/资产样例），
   **同步链路不受影响**（试跑明文断言）。真机：138****5678 ✅
3. **整库同步**（`POST /api/integ/jobs/bulk`）：选库→表多选（来自元数据）→逐表校验+试跑+发布，
   同名跳过/失败明细。真机：customers 批量任务 ✅

### T2
4. **数据比对**（`POST /api/integ/jobs/{id}/compare`）：源目行数差+抽样值集比对（only-in-source/target）+
   一致性结论。真机：正确暴露多次试跑累积导致的 60 vs 180 行差异 ✅
5. **BI 分析入口**：系统设置 `superset_url`（admin 配置）→ 侧边栏动态"BI 分析 (Superset)"菜单
   （外链新窗口），不自研报表引擎
6. **主机状态监控**（`GET /api/system/metrics`，psutil）：CPU/内存/磁盘/进程内存，监控页"主机状态"
   tab（10s 自动刷新开关）
7. **SQL Server 数据源**：mssql+pymssql（**渲染/映射层就绪，驱动延迟安装**——pymssql 需 FreeTDS
   编译，mac 失败时按提示 conda/预编译 wheel）；sqlglot 方言映射 **tsql**（修正：sqlglot 无 "mssql" 名）；
   NVARCHAR/DECIMAL 等映射；JDBC sqlserver 渲染

### 过程修复（3 个真实 bug）
- 文件导入表头去重逻辑恒加 `_1` 后缀（建表列名全错）→ 正确实现同名列去重
- GENERIC_MAP 扩展循环 dict 字面量同键覆盖（hive 族被 trino 段覆盖）→ 拆两个独立循环
- DataX/资产血缘里 `_re` 未定义、apigw 缩进、compare 缺 request 参数等

## 二·十九、全模块复查轮（P0+P1，2026-11-05）

复查范围：pyflakes + 13 页 console/4xx 扫描（全 0）+ 全量回归 + 浏览器实测。发现并完成：

| 级 | 问题 | 修复与验证 |
|---|---|---|
| P0 | **MC 同步路径 3 处未定义名**（`_sql_lit`/`execute_write`×2）——MC 任务必崩，此前无真实 MC 连接未暴露 | 导入修正 + **假 MC 端到端测试 2 例**（monkeypatch mc_client：MC源→sqlite 建表/增量/写入/post_sql、sqlite→MC 目标 append/truncate 拒绝）|
| P0 | **文件入仓无前端入口**（API 有、UI 无） | 集成页"文件导入"对话框：拖拽上传(CSV/TXT/JSON/Excel) + 目标库 + 表名自动填 + 冲突策略 + 结果卡。**浏览器真实 UI 全链**：注入 CSV→导入成功 3 行→查询脱敏联动 `138****1111` ✅ |
| P1 | AI 助手 prefill 仅首次挂载生效 | watch route.query 动态填充；实测：停留 AI 页点"AI 建同步"→输入框即时填充 ✅ |
| P1 | 整库同步/文件导入下拉选项疑未加载 | CUA 真实点击验证：选项完整（此前为合成点击伪影）；两对话框均加**懒加载保底** |
| P1 | 只读大文本无复制按钮 | DataX JSON / 比对样例加"复制"按钮 |
| 顺带 | mc_client 未用变量 | 清理 |

全量 **90/90 passed**（88+2），pyflakes 0 告警。

## 二·二十、P2 顺手项（2026-11-05）

| 项 | 验证 |
|---|---|
| 查询工作台保存查询**一键直跑**（▶ 按钮，点击回填保留） | 浏览器实测：直跑"SELECT COUNT(*) FROM orders"→ 成功 1 行 · 4ms，SQL 自动执行 ✅ |
| 多标签条**中键关闭标签**（el-tabs 不透出中键事件 → 模板 @auxclick + 事件委托，button=1 提取 tab id） | 浏览器实测：auxclick 派发"数据集成"标签 → 即时关闭，日志确认 handler 触发 ✅（首轮手动 addEventListener 未生效为构建时序伪影，模板绑定后稳定） |

全量 **90/90 passed**。至此复查轮 P0/P1/P2 全清。

## 三、测试证据

```
$ ./.venv/bin/python -m pytest tests/ -q
90 passed    （含 T1/T2 7 用例 + MC 修复 2 用例）
```

| 测试文件 | 覆盖 |
|---|---|
| test_sqlguard (6) | 只读白名单/ETL 白名单/LIMIT 包裹/血缘解析/类型映射知识库 |
| test_auth (6) | 登录/JWT/RBAC 403/停用用户/用户管理 |
| test_datasources_meta (2) | 数据源生命周期/采集/人工补录/术语/样例 |
| test_integ_sched (5) | 集成全生命周期（校验→试跑→发布→版本→DataX JSON→触发→数据核对→下线）/ETL+跨工作流依赖+血缘/运维（暂停恢复重跑+非法cron）/失败告警/权限 |
| test_agent_chat (5) | 对话查数/对话建同步+HITL确认/对话建ETL+执行/analyst 权限拒绝/找表 |
| test_apigw (4) | 发布+签名调用+审计/未授权+错签/限流/参数校验+非只读拒绝 |
| test_asset_audit (2) | 搜索/标签开关/审计 admin-only |
| test_query (5) | 查询工作台：只读执行/DML 拦截/历史/保存隔离/审计 |
| test_bugfix_review (4) | 版本回滚/任务删除级联/工作流删除守卫/pre+post_sql |

**架构红线测试化**：①AI 只产草稿，确认才上线（test_chat_create_sync_and_confirm）②AI 权限=用户权限（test_chat_permission_denied_for_analyst）③配置驱动调度（test_sync_job_full_lifecycle 断言 workflow.cron/next_fire）

## 三·五、Bug 审查轮（2026-11-04 第二轮）

对照 PRD 逐条核对 + pyflakes 静态扫描，发现并修复 6 个缺口/缺陷：

| # | 类型 | 问题 | 修复 |
|---|---|---|---|
| 1 | 功能缺失 | PRD"版本化可回滚"只有查看没有回滚 | 新增 `POST /jobs/{id}/versions/{vid}/rollback`（published 拒绝，需先下线）+ 版本抽屉"回滚至此"按钮 |
| 2 | 功能缺失 | draft/offline 任务与工作流无法删除 | 新增 `DELETE /jobs/{id}`（级联版本/血缘，无实例的伴随工作流同删）与 `DELETE /workflows/{id}?force=`（启用中/有实例需 force）+ UI 按钮 |
| 3 | bug | `pre_sql` 配置存在但从未执行（只有 post_sql） | run_sync 写入前执行 pre_sql（测试覆盖 pre+post） |
| 4 | 功能缺失 | PRD 元数据"定时采集"未实现 | LocalScheduler interval 采集任务（默认 24h，`META_COLLECT_HOURS=0` 可关），启动即跑一次 |
| 5 | bug | 启动时 waiting 实例的依赖轮询器不启动 | rebuild_jobs 启动 poller |
| 6 | bug | ①Agent 多工具调用逐条插 assistant 消息，违反 OpenAI 规范（真实 LLM 会 4xx）②MySQL DECIMAL 查询结果写入 ChatMessage.meta JSON 列抛 TypeError | ①合并为单条 assistant + 批量 tool 消息 ②`_json_safe` 清洗（Decimal/datetime/bytes） |

另：pyflakes 全仓扫描清理 8 处未使用变量/导入；UI 状态守卫验证（published 任务不显示删除按钮，draft 显示）。

## 四、浏览器视觉验收（8 页面截图通过）

登录页 / 智能问数（消息流+表格卡+确认卡+试跑统计+DDL+EXPLAIN）/ 数据源 / 数据集成（任务表+操作）/ 数据资产地图（搜索+空态）/ 表详情（字段+样例+**SVG 血缘图**）/ 调度运维（实例+告警 tab）/ 数据服务（API+日志 tab）

## 五、过程中发现并修复的缺陷（部分）

| 缺陷 | 修复 |
|---|---|
| Python 3.9 不支持 `str \| None` 注解 | 全部改 `Optional[]` |
| sqlglot 30 无 `exp.Explain`；Command 放行 ATTACH 有安全隐患 | 白名单改为 Select/Union/With/Describe/Show，其余一律拒绝 |
| validate 自动补列未落库 → 跨请求 DDL 为空 → 试跑失败 | validate/run_sync 持久化 columns |
| 试跑自动建表 DDL 带 MySQL 后缀，SQLite 报错 | suggest_target_ddl 按目标引擎方言生成 |
| ETL 目标表不存在直接失败 | SQL 节点执行前 CTAS LIMIT 0 自动建表 |
| 规则引擎目标表误取数据源名 / 裸工作流名解析为空 | 解析逻辑重写（分层命名优先 + 动词后取 token） |
| 节点日志原生 SQL 追加后 ORM 读不到 → error 为空 | run 后 refresh |
| 任务成功/发布后元数据不刷新 → 新表不进资产 | 节点成功后自动刷新目标源元数据；发布即采集 |
| 签名 secret 只存 hash 无法验签 | 改 Fernet 加密存储（与数据源凭据同策略） |

## 六、已知边界（与 PRD"明确不做"一致，非遗漏）

- 实时计算/BI 看板/多租户/K8s/机器学习平台：v1 范围外（PRD 第 3 节）
- 内置同步执行器为 SQLAlchemy 跨源复制；DataX 真实执行需配置 `DATAX_HOME`（JSON 渲染与版本管理已就绪）
- 规则引擎覆盖 6 类高频意图；复杂自由对话需配置 LLM（OpenAI 兼容环境变量即接）
- 前端聊天输入框 Enter 发送依赖 textarea keydown（自动化测试环境有输入怪癖，人工键入正常；发送按钮始终可用）
- Doris 自动建表 DDL 未含分桶/分区子句（生产建议人工复核 DDL，界面已展示建议）

## 七、使用入口

```bash
cd /Users/mac/Desktop/data-platform
./.venv/bin/uvicorn app.main:app --port 8100     # http://127.0.0.1:8100
# 账号: admin/admin123 · engineer/engineer123 · analyst/analyst123 · viewer/viewer123
./scripts/reset_demo.sh                           # 一键重置演示环境
./.venv/bin/python -m pytest tests/ -q            # 全量回归
```
