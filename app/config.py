"""平台配置：全部可用环境变量覆盖，默认值面向单机开箱即用。"""
import os
import secrets

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY_DIR = os.path.join(BASE_DIR, "deploy")
DATA_DIR = os.path.join(DEPLOY_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 数据库：默认 SQLite 单文件；生产可换 DATABASE_URL=mysql+pymysql://...
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'datapilot.db')}")

# JWT
SECRET_KEY = os.getenv("SECRET_KEY", "datapilot-dev-secret-change-in-prod")
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "720"))

# 凭据加密密钥：自动生成并持久化到 deploy/data/.fernet_key
FERNET_KEY = os.getenv("FERNET_KEY", "")
if not FERNET_KEY:
    _kf = os.path.join(DATA_DIR, ".fernet_key")
    if os.path.exists(_kf):
        FERNET_KEY = open(_kf).read().strip()
    else:
        from cryptography.fernet import Fernet
        FERNET_KEY = Fernet.generate_key().decode()
        with open(_kf, "w") as f:
            f.write(FERNET_KEY)
        try:
            os.chmod(_kf, 0o600)
        except OSError:
            pass

# 调度引擎：local（内置 APScheduler）| airflow（REST + factory DAG）
SCHEDULER_ENGINE = os.getenv("SCHEDULER_ENGINE", "local")
AIRFLOW_URL = os.getenv("AIRFLOW_URL", "http://localhost:8080")
AIRFLOW_DAGS_DIR = os.getenv("AIRFLOW_DAGS_DIR", "")  # factory DAG 配置目录（airflow 模式必填）
# Airflow 3.x 认证（JWT Bearer）取用优先级：AIRFLOW_TOKEN 直填
# > AIRFLOW_TOKEN_FILE（dags 共享卷里 factory 签发的 token 文件）
# > AIRFLOW_USER/AIRFLOW_PASSWORD 走 POST /auth/token 自动签发
AIRFLOW_TOKEN = os.getenv("AIRFLOW_TOKEN", "")
AIRFLOW_TOKEN_FILE = os.getenv("AIRFLOW_TOKEN_FILE", "")
AIRFLOW_USER = os.getenv("AIRFLOW_USER", "")
AIRFLOW_PASSWORD = os.getenv("AIRFLOW_PASSWORD", "")

# DataX：设置 DATAX_HOME 后 datax 节点用 datax.py 真实执行，否则内置执行器
DATAX_HOME = os.getenv("DATAX_HOME", "")

# LLM：OpenAI 兼容接口（DeepSeek/Qwen 等均可）。不配置时使用内置规则引擎
AI_BASE_URL = os.getenv("AI_BASE_URL", "")      # 如 https://api.deepseek.com/v1
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "deepseek-flash")

# 向量检索 RAG（可插拔 Embedder，见 docs/design/09）：
# 配置 AI_EMBED_MODEL(+BASE_URL，默认复用 AI_BASE_URL；key 复用 AI_API_KEY) 即启用语义检索；
# 不配置自动降级回词元匹配，行为与词元版完全一致。部署永不被 embedding 模型绑架。
AI_EMBED_BASE_URL = os.getenv("AI_EMBED_BASE_URL", "")
AI_EMBED_MODEL = os.getenv("AI_EMBED_MODEL", "")

# 聊天附件：落盘 DATA_DIR/uploads/chat，图片直接进 vision，文本读入 prompt
ATTACH_DIR = os.path.join(DATA_DIR, "uploads", "chat")
os.makedirs(ATTACH_DIR, exist_ok=True)
ATTACH_MAX_MB = int(os.getenv("ATTACH_MAX_MB", "10"))

# 告警默认发件（可在告警通道中逐条配置覆盖）
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))

# 查询安全
QUERY_TIMEOUT_S = int(os.getenv("QUERY_TIMEOUT_S", "30"))
QUERY_MAX_ROWS = int(os.getenv("QUERY_MAX_ROWS", "1000"))
CHAT_MAX_ROWS = 200

VERSION = "1.0.0"
