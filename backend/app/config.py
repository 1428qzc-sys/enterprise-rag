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
    # 逗号分隔的允许来源；"*" 表示放开（开发用）
    cors_origins: str = "*"

    # ---------------- 数据库 ----------------
    # 本地零配置默认 SQLite；docker-compose 中注入 PostgreSQL DSN
    database_url: str = "sqlite:///./data/enterprise_rag.db"

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
    rerank_enabled: bool = False
    rerank_provider: Literal["cross_encoder", "none"] = "none"
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

    # ---------------- RAG ----------------
    history_turns: int = 6      # 注入对话记忆的最近轮数
    max_context_chars: int = 6000

    # ---------------- 上传 ----------------
    upload_dir: str = "./data/uploads"
    max_upload_mb: int = 50

    @property
    def cors_origin_list(self) -> List[str]:
        value = (self.cors_origins or "").strip()
        if value in ("", "*"):
            return ["*"]
        return [o.strip() for o in value.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
