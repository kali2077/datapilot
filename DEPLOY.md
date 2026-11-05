# DataPilot 部署手册（DEPLOY.md）

> 配套文档：[README.md](README.md)（功能与快速体验）· [CONTRACTS.md](CONTRACTS.md)（API 契约）· [ACCEPTANCE.md](ACCEPTANCE.md)（验收记录）

## 一、环境要求

| 形态 | 最低配置 | 说明 |
|---|---|---|
| 单机体验（SQLite） | Python 3.9+，4GB 内存 | 60 秒起，含演示数据，无需任何外部组件 |
| 生产（Docker Compose） | 8C/16G，Docker 20+ | MySQL + Doris(FE/BE) + Airflow + DataX + app + nginx |
| 混合（推荐起步） | Python 3.9+ + 已有 MySQL/Doris | 平台裸跑，连公司现有库 |

## 二、方式一：单机快速启动（SQLite）

```bash
cd data-platform
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
./.venv/bin/uvicorn app.main:app --port 8100
# 浏览器: http://127.0.0.1:8100
```

首次启动自动完成：建表 → 种子数据（4 账号 / demo_source+demo_warehouse 演示库 / 类型映射知识库 / 术语 / 问数样例）→ 重建调度任务。

- 默认账号：`admin/admin123` · `engineer/engineer123` · `analyst/analyst123` · `viewer/viewer123`
- 前端：`web/dist` 存在时自动挂载（构建方式见§六）；无 dist 也可纯 API 使用（/docs 有 Swagger）
- 重置演示环境：`./scripts/reset_demo.sh`
- Dockerfile 依赖变更后构建前端：`cd web && npm run build`

## 三、方式二：生产部署（Docker Compose）

```bash
cd deploy
docker compose up -d        # MySQL + Doris(FE/BE) + Airflow + DataX + app + nginx
```

### 3.1 组件清单与端口

| 服务 | 端口 | 用途 |
|---|---|---|
| nginx | 80 | 前端 + API 反代（唯一入口） |
| app | 8000 | 平台后端（FastAPI） |
| mysql | 3306 | 平台元数据/配置库 |
| doris-fe / doris-be | 8030 / 9030 | OLAP（9030=MySQL 协议，数据源里 type 选 doris） |
| airflow | 8080 | 调度（SCHEDULER_ENGINE=airflow 时启用） |
| datax | — | DataX 可执行目录挂载（DATAX_HOME） |

### 3.2 首次初始化检查清单

1. `docker compose ps` 全部 Up（doris-be 首次注册 FE 需 1-2 分钟，`docker logs datapilot-doris-be` 观察）
2. Doris：`mysql -h127.0.0.1 -P9030 -uroot` → `SHOW FRONTENDS\G` 确认 FE Alive
3. Airflow：浏览器 8080 登录（账号见 compose 环境变量）→ 把 `deploy/airflow/factory_dag.py` 放入 dags 目录（compose 已自动拷贝）→ 平台侧设 `SCHEDULER_ENGINE=airflow` + `AIRFLOW_TOKEN`
4. 平台：浏览器 80 → 用 admin 登录 → 数据源页接入业务库 → 采集元数据 → 试跑首个同步任务

### 3.3 环境变量全表（app 服务 / 裸跑均适用）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///deploy/data/datapilot.db` | 生产改 `mysql+pymysql://user:pwd@host:3306/datapilot` |
| `SECRET_KEY` | 固定开发值 | **生产必改**（JWT 签名） |
| `FERNET_KEY` | 自动生成到 deploy/data/.fernet_key | 凭据加密密钥，**丢失=已存密码全部失效**，务必备份 |
| `JWT_EXPIRE_MINUTES` | 720 | 登录有效期（分钟） |
| `SCHEDULER_ENGINE` | `local` | `airflow`=接 Airflow 编排 |
| `AIRFLOW_URL` / `AIRFLOW_TOKEN` / `AIRFLOW_DAGS_DIR` | — | Airflow 适配器三件套（factory DAG 配置目录） |
| `DATAX_HOME` | 空（内置执行器） | 设为 DataX 目录后任务版本页渲染 JSON 可真实执行 |
| `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL` | 空（规则引擎兜底） | OpenAI 兼容接口，DeepSeek/Qwen 均可 |
| `META_COLLECT_HOURS` | 24 | 元数据定时采集间隔（小时），0=关闭 |
| `QUERY_TIMEOUT_S` / `QUERY_MAX_ROWS` | 30 / 1000 | 查询熔断与行数上限 |
| `SMTP_HOST` / `SMTP_PORT` | — | 告警邮件发件默认值（可在告警通道逐条覆盖） |

部署 compose 内另有：`MYSQL_ROOT_PASSWORD`/`MYSQL_PASSWORD`、`AIRFLOW_SECRET`/`AIRFLOW_PASSWORD`、`AIRFLOW_TOKEN`。

## 四、11 种数据源接入参数对照

| type | 必填 | 默认端口 | 说明 |
|---|---|---|---|
| mysql | host, database | 3306 | — |
| doris | host, database | 9030 | FE MySQL 协议 |
| starrocks | host, database | 9030 | 类型映射复用 doris 知识库 |
| oracle | host, database(=service_name) | 1521 | oracledb thin，无需装客户端；驱动 `pip install oracledb` |
| clickhouse | host, database | 8123 | HTTP 协议；驱动 `pip install clickhouse-sqlalchemy` |
| postgres | host, database | 5432 | 驱动 psycopg2（requirements 未含，按需装） |
| mssql | host, database | 1433 | 驱动 pymssql 需 FreeTDS 编译（mac 可能失败，用 conda/预编译 wheel）；sqlglot 方言=tsql |
| sqlite | params.path | — | 文件路径 |
| hive | host, params.auth | 10000 | auth=NONE 不需 sasl；LDAP/KERBEROS 需 sasl 依赖 |
| spark | host, params.auth | 10001 | 同 hive（HS2 协议） |
| trino | host, params.catalog | 8080 | 驱动 `pip install "trino[sqlalchemy]"` |
| maxcompute | username=AccessKeyId, password=AccessKeySecret, database=project, params.endpoint | — | pyodps 通道（驱动 `pip install pyodps`）；目标写入暂支持 append |

## 五、调度引擎切换

- 默认 `local`（APScheduler 内置，单机即用；含元数据定时采集 + 等待轮询 + 监控巡检）
- 切 Airflow：`SCHEDULER_ENGINE=airflow` + `AIRFLOW_URL/TOKEN` + `AIRFLOW_DAGS_DIR=/opt/airflow/dags`
  → 平台自动渲染 `workflows/wf_<id>.json`，由 `deploy/airflow/factory_dag.py` 动态生成 DAG；
  Airflow 侧仅编排，任务执行仍回调平台 API（需平台可达 token）

## 六、备份与恢复

```bash
# 每日备份（建议 crontab: 0 1 * * *）
./deploy/backup.sh
# 产物在 deploy/backups/，保留 KEEP_DAYS=14 天
```

- SQLite：`sqlite3 .backup` 热备；恢复 = 停服后用备份文件替换 deploy/data/datapilot.db
- MySQL：mysqldump gzip；恢复 `gunzip -c xx.sql.gz | mysql -h... datapilot`
- **FERNET_KEY 务必随备份**（deploy/data/.fernet_key），否则已存密码/AK-SK 无法解密

## 七、升级

1. 备份（§六）
2. 替换代码目录（app/ web/dist）
3. `pip install -r requirements.txt`（有新依赖时）
4. 重启 app——启动时自动 `create_all + run_migrations`（增量列自动补齐，见 db.py MIGRATIONS）
5. 前端更新：`cd web && npm run build`（产物 dist 由后端挂载，无需重启）

## 八、常见问题

| 现象 | 处理 |
|---|---|
| 登录报 500 / JWT 错误 | SECRET_KEY 与历史 token 不匹配属正常，重新登录 |
| 数据源测试连接失败 "No module named pymssql/psycopg2" | 对应驱动不在 requirements 默认集，按§四表格 pip 安装 |
| pymssql / sasl 编译失败 | conda install，或下载预编译 wheel；hive 用 auth=NONE 可绕过 sasl |
| Oracle 目标表不存在时报错 | 设计如此：Oracle 无 IF NOT EXISTS，请先手工建表（页面试跑会给出建议 DDL） |
| Airflow 模式下任务不出现 | 检查 AIRFLOW_DAGS_DIR 是否挂载、factory_dag.py 是否在 dags 目录、平台 API 对 Airflow 容器可达 |
| Doris 建表报 ENGINE 相关错误 | 自动建表已带 MergeTree 仅针对 ClickHouse；Doris 目标建议手工建分区表后用 append |
| 监控巡检没跑 | 规则需 enabled 且 cron 合法；启动日志无报错则等下一 cron 点，或页面"立即检查" |
| 前端改了没生效 | `cd web && npm run build`（后端挂载的是 dist 产物） |

## 九、安全清单（上线前过一遍）

- [ ] `SECRET_KEY` / `FERNET_KEY` / 各组件密码全部改为强随机
- [ ] admin 初始密码已改
- [ ] nginx 仅暴露 80/443，Doris/Airflow/MySQL 端口不对公网开放
- [ ] 备份 crontab 已配置且验证过恢复
- [ ] 数据源凭据均为 Fernet 加密存储（默认即加密）
