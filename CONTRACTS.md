# DataPilot API 契约（前端对接用）

Base URL: `http://127.0.0.1:8100`（开发模式 Vite proxy 到 8100）
认证: `Authorization: Bearer <token>`；错误返回 `{"detail": "..."}`（HTTP 4xx/5xx）。
登录后默认账号: admin/admin123, engineer/engineer123, analyst/analyst123, viewer/viewer123
角色: admin > engineer > analyst > viewer（数字为权限层级）

## 页面 → API 对照

### 1 登录 / 用户
- POST `/api/auth/login` {username,password} → {token, user{id,username,display_name,role}}
- GET `/api/auth/me` → 当前用户
- GET `/api/auth/users` (admin)；POST `/api/auth/users` {username,password,display_name,role}
- PUT `/api/auth/users/{id}` {display_name?,role?,status?,password?}

### 2 数据源管理（engineer+）
- 支持类型（**11 种**）：mysql / doris / starrocks / oracle / clickhouse / postgres / sqlite /
  **hive**(HS2 10000) / **spark**(thrift 10001) / **trino**(8080) / **maxcompute**(pyodps)
  - maxcompute：username=AccessKeyId、password=AccessKeySecret、database=project、params.endpoint 必填
  - hive/spark：params.auth=NONE|LDAP|KERBEROS（KERBEROS 需 sasl 依赖）
  - trino：params.catalog / params.schema
- GET `/api/datasources` → [{id,name,type,host,port,database,username,has_password,params,description,owner_id,status,updated_at}]
- POST `/api/datasources` {name,type:mysql|doris|postgres|sqlite,host,port,database,username,password,params,description}（连接测试失败会 400）
- PUT `/api/datasources/{id}`；DELETE `/api/datasources/{id}`
- POST `/api/datasources/{id}/test` → {ok,message}
- POST `/api/datasources/{id}/collect` → {tables,columns}（触发元数据采集）

### 2.5 Hadoop 平台（engineer 管理；全部登录角色可看）
- GET/POST/PUT/DELETE `/api/hadoop/clusters[/{id}]`（集群注册：五端点+Kerberos）
- POST `/api/hadoop/clusters/{id}/test` → {components:{hdfs/hive_hs2/metastore/spark/trino:{ok,message}},kerberos}
- POST `/api/hadoop/clusters/{id}/derive` {kind:hive|spark|trino,name,database,username,password}
  → 派生数据源（预填+params.cluster_id+auth 按平台 Kerberos）
- POST `/api/hadoop/clusters/{id}/keytab`（multipart file，<2MB，Fernet 目录存储）

### 3 元数据 / 资产 / 地图
- GET `/api/meta/tables?datasource_id=&keyword=&page=&size=` → {total,items[{id,name,datasource,comment,manual_desc,owner_name,row_count,last_collected_at}]}
- GET `/api/meta/tables/{id}` → {columns[{name,type,comment,manual_desc,is_pk,samples}]}
- PUT `/api/meta/tables/{id}` {manual_desc?,owner_name?}（engineer）
- GET/POST/PUT/DELETE `/api/meta/glossary`（术语表，写需 engineer）
- GET/POST/PUT/DELETE `/api/meta/fewshot` {question,sql,datasource_id,enabled}
- GET `/api/asset/search?keyword=` → {tables[{id,name,datasource,comment,owner,rows,tags[]}],apis[]}
- GET `/api/asset/tags`；POST `/api/asset/tags` {name,color}；DELETE `/api/asset/tags/{id}`
- POST `/api/asset/tables/{id}/tags` {tag_id,on:bool}
- GET `/api/asset/tables/{id}` → 表详情（columns/tags/upstream[{table,datasource,job_type,workflow_id}]/downstream/sample{columns,rows}/related_workflows）
- GET `/api/asset/map/graph?datasource_id=&table=&depth=2` → {nodes[{id,datasource,table}],edges[{source,target,workflow_id,job_type}],center}

### 3.5 数据查询工作台（所有登录角色）
- POST `/api/query/run` {datasource_id,sql,limit} → {columns,rows,row_count,truncated,cost_ms,sql,datasource}
  - 后端 sqlguard 只读白名单（SELECT/WITH/SHOW/EXPLAIN），自动包裹 LIMIT，超时熔断，写入审计
- GET `/api/query/history?limit=` → 用户查询历史（每用户保留 200 条）；DELETE `/api/query/history` 清空
- GET/POST `/api/query/saved`、DELETE `/api/query/saved/{id}` → 已保存查询（个人）

### 4 数据集成（engineer+）
- GET `/api/integ/jobs?status=` → [{id,name,status,current_version,source{id,name,type,table,query},target{...},config,updated_at}]
- POST `/api/integ/jobs` {name,source_datasource_id,source_table,source_query,target_datasource_id,target_table,config}
  - config: {columns:[{source,target,source_type,target_type}],write_mode:append|truncate_insert|replace,partition_col,increment:{enabled,column,mode},pre_sql,post_sql}
- GET `/api/integ/jobs/{id}` → +issues[{level:info|warning|error,message}]+ddl
- PUT `/api/integ/jobs/{id}`（draft 才可改）
- POST `/api/integ/jobs/{id}/validate` → {ok,issues,ddl}
- POST `/api/integ/jobs/{id}/trial` → {stats{read_rows,written_rows,duration_ms,auto_created},columns,rows}
- POST `/api/integ/jobs/{id}/publish` {cron:"0 2 * * *",depends_on:[wfId]} → {workflow_id,workflow,version}
- POST `/api/integ/jobs/{id}/offline`
- GET `/api/integ/jobs/{id}/versions`；GET `/api/integ/jobs/{id}/versions/{vid}/datax` → {datax_json}
- POST `/api/integ/jobs/{id}/versions/{vid}/rollback`（published 需先下线）
- DELETE `/api/integ/jobs/{id}`（仅 draft/offline；级联版本/血缘）
- GET/POST/DELETE `/api/integ/typemappings`（admin 写）

### 5 数据开发 + 调度
- GET `/api/sched/workflows` → [{id,name,description,schedule_cron,enabled,depends_on,job_type:sync|etl,sync_job_id,owner_id,next_fire}]
- POST `/api/sched/workflows` {name,description,schedule_cron,depends_on,enabled,nodes:[{name,node_type:sql|datax|python,config,upstream:[nodeId]}]}
  - sql 节点 config: {datasource_id,sql,target_table}；python: {script}；datax: {sync_job_id}
  - 返回含 problems[]（SQL 校验问题）
- GET `/api/sched/workflows/{id}` → +nodes[]
- PUT `/api/sched/workflows/{id}` {name?,description?,schedule_cron?,depends_on?,enabled?}
- POST `/api/sched/workflows/{id}/trigger` → {instance_id,status}
- DELETE `/api/sched/workflows/{id}?force=true|false`（启用中/有实例需 force）
- GET `/api/sched/instances?workflow_id=&status=&page=&size=` → {total,items[{id,workflow_name,trigger_type,logical_date,status,started_at,finished_at,error,triggered_by}]}
- GET `/api/sched/instances/{id}` → +nodes[{id,node_id,name,node_type,status,duration_ms,started_at,log}]
- POST `/api/sched/instances/{id}/rerun?only_failed=true|false`
- POST `/api/sched/nodes/{run_id}/rerun`
- POST `/api/sched/dev/sql/validate` {datasource_id,sql,target_table} → {etl_ok,etl_msg,readonly_ok,sources[],target}
- POST `/api/sched/dev/sql/explain` {datasource_id,sql,target_table} → {ok,plan[]}
- 告警: GET `/api/sched/alert-channels`；POST {name,type:email|webhook,config,enabled}；PUT/DELETE `/{id}`；GET `/api/sched/alert-records`
  - email config: {to,smtp_host,smtp_port,username,password}；webhook config: {url,secret?}

### 5.5 数据监控告警（engineer 管理；全部登录角色可看）
- 规则类型：row_count_fluctuation{pct}（基线自动建立）/ row_count_threshold{min,max} / null_rate{pct} / unique_check{} / freshness{hours} / sql_check{sql 只读，行数>0 即告警} / task_duration{workflow_name,max_minutes}
- GET `/api/monitor/rules`；POST/PUT/DELETE `/api/monitor/rules[/{id}]`（CRUD 自动注册/移除 cron 调度）
  - 规则字段含 group_name（分组）、mute_hours（告警静默小时数，0=每次都通知）
- POST `/api/monitor/rules/{id}/run` → {status:passed|failed|error, metric, message, cost_ms}
- GET `/api/monitor/checks?rule_id=&status=&page=` → {total,items[]}
- GET `/api/monitor/trend?days=7&rule_id=` → {days:[{day,passed,failed,error}],total}
- GET `/api/monitor/summary` → {rules_total,rules_enabled,checks_24h,failed_24h,error_24h,pass_rate,alerts_24h,failing_rules[]}
- 失败 → 复用调度运维的告警通道（email/webhook），AlertRecord 标题 `[DataPilot] 监控告警[severity]: 规则名`
  - **静默窗口**：mute_hours 内同一规则不重复通知（检查记录照常落库，message 追加"静默期内"）

### 6 智能问数 / AI 对话（所有登录用户）
前端入口：左侧导航「智能应用」→「智能问数」**子导航**（问数对话 /chat · SQL 示例 /fewshots · 术语库 /glossary）
与「AI 助手」/ai-assistant；顶部标签条可多页并存切换。
SQL 示例与术语库为问数 RAG 知识库管理页（术语族展开 + few-shot 注入见 build_context）。
- GET `/api/chat/sessions`；POST `/api/chat/sessions`；DELETE `/api/chat/sessions/{id}`（级联删除消息与附件文件）
- GET `/api/chat/sessions/{id}/messages` → [{id,role,content,card,attachments,confirm_token,confirm_status,provider,ts}]
- POST `/api/chat/sessions/{id}/send` {text, page?, attachments?: [attachment_id]} → {message_id,content,card,confirm_token,confirm_status,provider,error}
  - page: 当前页面路由（如 /integration），注入 system 提示让 AI 感知上下文
  - attachments: 附件 id 列表（≤6，须属于当前会话；文本/解析类内容注入 prompt，图片走 vision content）
  - card.type: table{columns,rows,sql,chart{x,y[]},datasource} / tables{tables[]} / schema{columns[]} / confirm{action,title,display(键值对),payload,trial,issues,ddl,sql,plan} / tables+text / job_link{workflow_id,job_id} / instance_link / api_link / instance{instance_id,workflow,status,trigger_type,error,nodes[]} / monitor{rules_total,checks_24h,failed_24h,failing[]} / monitor_check{rule,status,metric,message}
  - AI 工具共 13 个：query_data / search_metadata / get_table_schema / create_sync_job / create_etl_job / run_job / pause_job / resume_job / rerun_job / publish_api / get_instance_detail / monitor_overview / run_monitor_check
- POST `/api/chat/sessions/{id}/attachments` multipart(file) → {id,name,type(image/table/doc/text),size,url,meta{rows,cols|pages}}
  - 支持：图片 png/jpg/webp/gif；表格 xlsx（openpyxl 解析，全列名+前 100 行）；文档 pdf（pypdf，限 30 页）/docx；文本 txt/md/csv/tsv/json/log/sql/py/sh/yaml/xml/html/ini/conf/toml
  - 限额：文本类 ≤2MB，其余 ≤10MB（ATTACH_MAX_MB 可覆盖）；解析失败返回 400
- GET `/api/chat/attachments/{id}?token=` → 文件流（img src 等 URL 场景用 query token 鉴权，Owner 或 admin）
- POST `/api/chat/sessions/{id}/confirm` {message_id,token} → {ok,summary,card,message_id}
- POST `/api/chat/sessions/{id}/cancel` {message_id,token}

### 7 数据服务（engineer 管理；公共调用无需 JWT）
- GET/POST/PUT/DELETE `/api/apigw/apis`（draft 才可改；POST 校验 SQL 只读）
- POST `/api/apigw/apis/{id}/publish` / `/offline`
- POST `/api/apigw/apis/{id}/test-execute` {params:{}} → {columns,rows,row_count,truncated,cached}
- GET `/api/apigw/apis/{id}/doc`
- GET `/api/apigw/apps`；POST `/api/apigw/apps` {app_name,rate_limit,ip_whitelist} (admin) → 返回 {app_key,app_secret}(仅一次)
- PUT `/api/apigw/apps/{id}/status` {status,rate_limit?}；POST `/api/apigw/apps/{id}/grants` {api_id,expire_at?}
- GET `/api/apigw/grants/{app_id}`；GET `/api/apigw/logs?api_id=&page=`
- 公共调用: POST `/api/v1/query/{code}` headers {X-App-Key,X-Timestamp,X-Sign} body {params:{}}
  - X-Sign = HMAC-SHA256(secret, sorted-JSON 字符串) 其中 payload={"app_key","timestamp"(字符串秒),"params"}
  - python: `json.dumps(payload, sort_keys=True, separators=(',',':'))` 后 hmac-sha256 hex

### 8 审计（admin）
- GET `/api/audit/logs?user=&action=&source=&page=&size=` → {total,items[{username,action,resource_type,resource_id,detail,source,ip,ts}]}

## 前端技术要求
- Vue3 + Vite + Element Plus + echarts + axios；node 24 已装
- 路由按角色过滤菜单：admin 全部；engineer 除用户管理外全部；analyst：问数/资产地图/查询/数据服务(只读文档)；viewer：问数/资产地图
- 图表: card.chart 存在时 echarts 柱状图；confirm 卡片用 el-card + 键值展示 + 确认/取消按钮
- 血缘图: 用 SVG 画分层 DAG（中心表居中，上游左、下游右），不引入额外依赖
- 构建: `npm run build` 输出 `web/dist`（后端自动挂载），Vite dev proxy `/api` → 8100
- 登录态存 localStorage，401 全局拦截跳登录页
