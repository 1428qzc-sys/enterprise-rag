"""全局配置。

所有配置项均可通过环境变量或项目根目录 / backend 目录下的 `.env` 文件覆盖，
字段名（大小写不敏感）即环境变量名，例如字段 ``llm_api_key`` 对应 ``LLM_API_KEY``。
"""

from functools import lru_cache
from typing import List, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # 依次尝试 backend/.env 与项目根 .env，后者便于配合 docker-compose
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------------- 应用 ----------------
    app_name: str = "Enterprise RAG"
    api_prefix: str = "/api"
    environment: str = "development"
    log_level: str = "INFO"
    readiness_timeout_seconds: float = 2.0
    health_external_checks: bool = True
    # 逗号分隔的允许来源；"*" 表示放开（开发用）
    cors_origins: str = "*"

    # ---------------- 服务安全基线 ----------------
    request_timeout_seconds: float = 60.0
    rate_limit_requests_per_minute: int = 600
    rate_limit_backend: Literal["memory", "redis"] = "memory"
    rate_limit_redis_failure_mode: Literal["fail_open", "fail_closed"] = "fail_open"
    rate_limit_key_prefix: str = "enterprise-rag:ratelimit"
    redis_url: str = "redis://localhost:6379/0"
    worker_thread_tokens: int = 100

    # ---------------- 认证 / 多租户 ----------------
    # 生产环境必须覆盖为高熵随机值；默认值仅用于本地 smoke 与测试。
    auth_secret_key: str = "dev-change-me-enterprise-rag"
    access_token_expire_minutes: int = 480
    bootstrap_tenant_name: str = "Demo Enterprise"
    bootstrap_tenant_slug: str = "demo"
    bootstrap_admin_email: str = "admin@example.com"
    bootstrap_admin_password: str = "ChangeMe123!"
    bootstrap_admin_name: str = "系统管理员"

    # ---------------- 数据库 ----------------
    # 本地零配置默认 SQLite；docker-compose 中注入 PostgreSQL DSN
    database_url: str = "sqlite:///./data/enterprise_rag.db"
    database_auto_create: bool = True
    db_pool_size: int = 20
    db_max_overflow: int = 40
    db_pool_timeout_seconds: float = 30.0

    # ---------------- 向量库 ----------------
    # memory：进程内 numpy 余弦，零依赖便于本地/测试；qdrant：生产
    vector_backend: Literal["memory", "qdrant"] = "memory"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""
    qdrant_collection_prefix: str = "kb_"

    # ---------------- Embedding ----------------
    # openai：任意 OpenAI 兼容 /embeddings 端点（OpenAI、硅基流动、本地 vLLM 等）
    # ollama：本地 Ollama    fake：确定性哈希向量（仅测试）
    embedding_provider: Literal["openai", "ollama", "fake"] = "openai"
    embedding_model: str = "text-embedding-3-small"
    embedding_base_url: str = "https://api.openai.com/v1"
    embedding_api_key: str = ""
    embedding_dim: int = 1536
    embedding_batch_size: int = 32

    # ---------------- LLM ----------------
    # openai：任意 OpenAI 兼容 /chat/completions（OpenAI、DeepSeek 等）
    # ollama：本地 Ollama    echo：回显（仅测试）
    llm_provider: Literal["openai", "ollama", "echo"] = "openai"
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024

    # ---------------- Ollama ----------------
    ollama_base_url: str = "http://localhost:11434"

    # ---------------- 重排 ----------------
    rerank_enabled: bool = True
    rerank_provider: Literal["lexical", "cross_encoder", "none"] = "lexical"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_top_n: int = 5

    # ---------------- 分块 ----------------
    chunk_size: int = 800
    chunk_overlap: int = 120

    # ---------------- 检索 ----------------
    vector_top_k: int = 20      # 向量召回条数
    bm25_top_k: int = 20        # BM25 召回条数
    hybrid_top_k: int = 8       # 融合/重排后进入上下文的条数
    rrf_k: int = 60             # RRF 平滑常数
    vector_min_score: float = 0.05  # 低于此余弦分数不视为有效向量证据

    # ---------------- RAG ----------------
    history_turns: int = 6      # 注入对话记忆的最近轮数
    max_context_chars: int = 6000
    # 只有达到该确定性词项相似度的分块才可进入生成上下文；检索预览仍返回全部候选。
    # 默认值由固定评估集的可回答/无答案样本分布确定，可通过环境变量调整。
    rag_min_evidence_score: float = 0.2

    # ---------------- 上传 ----------------
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 50
    upload_read_chunk_bytes: int = 1024 * 1024
    ingestion_max_attempts: int = 3

    # ---------------- 外部 URL 抓取防护 ----------------
    url_fetch_timeout_seconds: float = 10.0
    url_fetch_max_redirects: int = 3
    url_fetch_max_mb: int = 5

    @property
    def cors_origin_list(self) -> List[str]:
        value = (self.cors_origins or "").strip()
        if value in ("", "*"):
            return ["*"]
        return [o.strip() for o in value.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def validate_runtime(self) -> None:
        """生产环境拒绝已知的本地演示安全配置。"""

        if self.environment.lower() not in {"production", "prod"}:
            return
        problems: list[str] = []
        if len(self.auth_secret_key) < 32 or self.auth_secret_key in {
            "dev-change-me-enterprise-rag",
            "local-demo-auth-secret-change-before-production",
        }:
            problems.append("AUTH_SECRET_KEY 必须是至少 32 字符的独立随机值")
        if self.bootstrap_admin_password == "ChangeMe123!":
            problems.append("BOOTSTRAP_ADMIN_PASSWORD 不能使用演示默认值")
        if self.cors_origin_list == ["*"]:
            problems.append("CORS_ORIGINS 不能在生产环境使用 *")
        if self.database_auto_create:
            problems.append("DATABASE_AUTO_CREATE 必须在生产环境关闭并使用 Alembic")
        if self.is_sqlite or "ragpwd-local-demo" in self.database_url:
            problems.append("生产环境必须使用非演示凭据的外部 PostgreSQL")
        if self.vector_backend != "qdrant":
            problems.append("生产环境 VECTOR_BACKEND 必须使用 qdrant")
        if self.rate_limit_backend != "redis" or self.rate_limit_redis_failure_mode != "fail_closed":
            problems.append("生产环境限流必须使用 Redis fail_closed")
        if self.embedding_provider == "fake":
            problems.append("生产环境不能使用 fake Embedding")
        if self.llm_provider == "echo":
            problems.append("生产环境不能使用 echo LLM")
        if self.embedding_provider == "openai" and not self.embedding_api_key:
            problems.append("OpenAI-compatible Embedding 必须配置 API Key")
        if self.llm_provider == "openai" and not self.llm_api_key:
            problems.append("OpenAI-compatible LLM 必须配置 API Key")
        if problems:
            raise RuntimeError("生产配置校验失败：" + "；".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
