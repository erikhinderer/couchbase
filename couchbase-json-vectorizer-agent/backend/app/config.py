"""Central configuration, loaded from environment variables (see env.example)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- App ---
    app_name: str = "Couchbase JSON Vectorizer Agent"
    environment: str = "development"
    log_level: str = "INFO"
    cors_origins: list[str] = ["https://localhost", "http://localhost:5173", "http://localhost:3000"]

    # --- Vector search capability requirements ---
    # Native FTS vector-typed fields (and therefore Couchbase Vector Search) require
    # Couchbase Server Enterprise Edition 7.6.0+ or Capella; Community Edition cannot
    # run vector search at all (see core/validator.py). This is a hard requirement for
    # this agent -- unlike the migration agent's memory store, there is no CE fallback
    # here because the whole point of the product is native vector search on the docs.
    min_vector_search_version: str = "7.6.0"

    # --- Local LLM: Qwen 3, 8B params, served via Ollama-compatible API ---
    # Powers the in-app assistant panel only (explaining validation results, helping
    # pick an embedding model, summarizing job health). It does NOT generate the
    # document embeddings themselves -- that's the user-selected HuggingFace model,
    # served by embedding-service.
    qwen_base_url: str = "http://qwen-service:11434"
    qwen_model_name: str = "qwen3:8b"
    qwen_request_timeout_s: int = 120

    # --- Embedding service (sentence-transformers models, HTTP API) ---
    embedding_service_base_url: str = "http://embedding-service:8500"
    embedding_request_timeout_s: int = 60

    # --- Vectorizer engine ---
    vectorizer_state_file: str = "/data/state/jobs.json"
    default_batch_size: int = 32
    default_poll_interval_seconds: float = 3.0
    default_vector_field_name: str = "embedding"
    max_operation_log_entries: int = 500
    # naive text-extraction cap: how many characters of flattened JSON text to embed
    # per document (keeps very large documents from blowing up embedding latency).
    max_embed_text_chars: int = 8000


@lru_cache
def get_settings() -> Settings:
    return Settings()
