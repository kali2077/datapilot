# 10 · 数据源扩展二期：Hive / Trino / Spark / MaxCompute + Hadoop 平台接入（规划稿）

> 状态：**规划定稿，分三期实施**（P0 可立即开工）。
> 前置：数据源架构已支撑 7 种类型（见 ACCEPTANCE 二·十三），本期沿用同一适配模式
> （build_url / sqlguard 方言 / 类型映射 / DataX 渲染 / 建表方言 / 前端选择器）。

## 一、结论

| 目标源 | 可接 | 接入形态 | 纯 Python | 建议 |
|---|---|---|---|---|
| **Trino** | ✅ 最顺 | SQLAlchemy 官方方言（trino://） | ✅ | **P0** |
| **Spark**（Spark Thrift Server / Kyuubi） | ✅ | 复用 Hive HS2 客户端（thrift 协议同族） | ⚠️ 同 Hive | **P0**（与 Hive 同批） |
| **Hive**（HiveServer2） | ✅ | SQLAlchemy dialect（pyhive） | ⚠️ 见风险 | **P0** |
| **MaxCompute（ODPS）** | ✅ | pyodps 专用执行器（非 SQLAlchemy） | ✅ | **P1** |
| **Hadoop 平台配置页** | ✅ | 集群注册表 + 组件连通 + 派生数据源 | ✅ | **P2** |

> 术语确认：MaxCompute 即阿里云 ODPS；"rspark" 按 **Spark（Spark Thrift Server / Kyuubi，thrift 协议）**
> 理解——如指的是别的东西（如 Spark on Kubernetes 提交作业），实施前确认。

## 二、各源技术选型与部署成本矩阵

| 源 | Python 依赖 | 默认端口 | 部署额外安装 | 认证 |
|---|---|---|---|---|
| Trino | `trino[sqlalchemy]`（纯 Python） | 8080 | pip 即装 | 用户名/密码、无认证 |
| Hive | `pyhive[hive]` + `thrift`（sasl 见风险） | 10000 | pip（sasl 可能有编译坑，见§五） | NONE / LDAP / Kerberos |
| Spark | 同 Hive（HS2 协议） | 10001（Thrift）/ Kyuubi 10009 | 同上 | 同上 |
| MaxCompute | `pyodps`（纯 Python） | HTTP | pip 即装 | AK/SK（AccessKeyId/Secret）+ endpoint |
| Hadoop 平台 | WebHDFS(HTTP) 探活 + 上述客户端 | 9870/8020 等 | 无新增 | Kerberos 可选（keytab） |

**红线不变：接不上的引擎自动不可选，平台其余功能零受影响；本地无集群时全部渲染/映射层可单测（与 Oracle/StarRocks/ClickHouse 同模式）。**

## 三、逐源支持点（沿用既有适配清单）

### Trino（P0，最顺）
- 方言：sqlglot 原生 `trino`；只读为主（INSERT 取决于 catalog）
- 元数据：SQLAlchemy inspector（show tables / describe）
- 类型映射：varchar/decimal(p,s)/timestamp/map/row → 各目标引擎
- DataX：rdbmsreader + trino-jdbc（io.trino:trino-jdbc）
- 注意：查询需带 catalog 前缀，URL 形如 `trino://user@host:8080/catalog/schema`

### Hive / Spark（P0，同批实现）
- 连接：HiveServer2 thrift；auth=NONE/LDAP 走普通链路，Kerberos 走 keytab（P2 平台页统一配）
- 元数据：inspector 弱（Hive 方言部分支持）——**采集降级策略**：inspector 失败时回退
  `show tables` + `desc formatted <t>` 解析（hive/spark 专用分支）
- 类型映射：string/tinyint/double/decimal(p,s)/timestamp/array/map/struct（复杂类型→目标 STRING/JSON）
- 自动建表：Hive/Spark 方言（`PARTITIONED BY` / `STORED AS PARQUET` 提示注释）
- DataX：**hdfsreader/hdfswriter**（Hive 表底层是 HDFS 文本/Parquet；平台页提供 NameNode 地址）；
  JDBC 途径作为 spark/kyuubi 备选
- 写入：内置执行器 INSERT 对 Hive 可用但慢——同步任务目标为 Hive 时 UI 提示"建议 DataX/HDFS 路径"

### MaxCompute（P1）
- pyodps 专用 `MaxComputeExecutor`：execute_sql（读）、tunnel 下载采样、inspector（list_tables/table_schema）
- 注册为 engine_registry 的特例（build_url 不适用，按 ds.type 分派 executor）
- 类型映射：BIGINT/DOUBLE/STRING/DATETIME/DECIMAL → 各目标引擎
- DataX：**odpsreader / odpswriter**（阿里官方插件）渲染：project/table/partition/accessId/accessKey/endpoint
- 配置字段：endpoint（如 http://service.cn-hangzhou.maxcompute.aliyun.com/api）、project、AK/SK

### Hadoop 平台配置页（P2）
**概念**：数据源之上的"集群对象"，一次注册、多组件派生。

```
Hadoop 平台（新页面，数据源管理下）
├─ 集群注册：名称 / HDFS(NameNode WebHDFS+RPC) / HiveServer2 / Metastore thrift
│             / Spark Thrift / Trino endpoint / YARN RM（展示）
├─ Kerberos（可选）：realm + principal + keytab 上传（Fernet 加密存储）
├─ 组件连通测试：WebHDFS HTTP 探活 / HS2 connect / metastore socket / trino ping
└─ 一键派生数据源：生成 hive_xxx / spark_xxx / trino_xxx 数据源（预填+引用平台 Kerberos 配置）
```

- 表结构：`hadoop_cluster(id, name, hdfs_uri, hs2_uri, metastore_uri, spark_uri, trino_uri, kerberos JSON, owner)`
- 数据源表加 `cluster_id` 外键引用（向后兼容：为空则独立认证）
- DataX hdfs 读写从所属平台取 NameNode 配置；Kerberos keytab 供 pyhive/sasl 使用
- UI：数据源管理页新增"平台"tab 或独立页面（待实施时定）

## 四、分期计划

| 期 | 内容 | 工作量 | 交付判据 |
|---|---|---|---|
| **P0** | Trino + Hive + Spark 三数据源（URL/方言/映射/DataX JDBC/前端/测试） | ~1 天 | 新类型测试 ≥9 个全绿；测试库（docker trino/hive）或渲染层单测通过 |
| **P1** | MaxCompute（pyodps executor + odpsreader/writer 渲染 + 映射） | ~1 天 | 真实 ODPS project 试跑 100 行（或按官方 mock） |
| **P2** | Hadoop 平台配置页（集群注册/连通/派生/Kerberos） | 1~1.5 天 | 注册平台→派生 hive 数据源→试跑成功 |

## 五、风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| **pyhive 的 sasl C 扩展编译失败**（mac/部分 Linux 常见） | Hive/Spark 安装受阻 | ① auth=NONE 不走 SASL ② HS2 HTTP transport 模式 ③ 预编译 wheel / conda ④ 文档写明三种装法 |
| Hive 的 SQLAlchemy inspector 兼容性弱 | 元数据采集不全 | show tables + desc formatted 解析的专用采集分支（P0 内做） |
| Trino/Hive 写入能力取决于底层 catalog | ETL 任务可能不可写 | 校验链明确报错提示；目标为 Hive 时提示 HDFS 路径 |
| MaxCompute 非 SQLAlchemy 体系 | 与现有执行器抽象不一致 | executor 按 type 分派（已有 datax/local 双执行器先例） |
| 真机联调需要真实集群 | 本地只能测到渲染/映射层 | 与 Oracle/StarRocks/ClickHouse 同承诺：代码就绪，接入时填地址即验 |

## 六、明确不做（本期）

- HDFS 文件浏览器/作业提交 UI（YARN 提交、Spark job 提交界面）
- Ranger/Sentry 权限集成
- 多集群联邦查询（Trino catalog 联邦天然具备，直接建多个 Trino 数据源即可）
