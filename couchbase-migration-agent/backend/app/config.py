"""Central configuration, loaded from environment variables (see env.example)."""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- App ---
    app_name: str = "Couchbase Migration Agent"
    environment: str = "development"
    log_level: str = "INFO"
    # Comma-separated list of browser origins allowed to call this API. Defaults cover
    # localhost and 127.0.0.1 on both the Vite dev port and the built frontend's port. If
    # you access the UI from anywhere else -- a LAN IP, a different hostname, a remote box
    # -- add that exact origin here via the CORS_ORIGINS env var, or every fetch() call
    # from the browser will fail with a generic, CORS-unaware "NetworkError"/"Failed to
    # fetch" that gives no hint it's a CORS problem.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    # --- Supported Couchbase server range ---
    min_supported_version: str = "7.2.0"
    max_supported_version: str = "8.0.2"

    # --- cbbackupmgr / CLI tooling ---
    couchbase_bin_dir: str = "/opt/couchbase/bin"  # cbbackupmgr, cbimport, cbrestore live here
    # cbbackupmgr needs its own bundled libcrypto/libssl on its LD_LIBRARY_PATH, but that
    # path must NOT be set container-wide (it breaks Python's own `import ssl` -- see the
    # comment in backend/Dockerfile) -- so it's only applied to the cbbackupmgr subprocess
    # itself in BackupManager._run() (app/core/backup_manager.py).
    couchbase_lib_dir: str = "/opt/couchbase/lib"
    backup_storage_dir: str = "/data/backups"

    # --- Agent memory: Couchbase Enterprise Edition (free for dev/test) ---
    memory_cb_connection_string: str = "couchbase://couchbase-memory"
    memory_cb_username: str = "Administrator"
    memory_cb_password: str = "password"
    memory_cb_bucket: str = "agent_memory"
    memory_cb_scope: str = "agent"
    memory_cb_collection: str = "episodes"
    memory_cb_vector_index: str = "agent_memory_vector_idx"
    # Must match the actual output length of QWEN_EMBEDDING_MODEL_NAME's /api/embeddings
    # response (only matters on Enterprise/Capella, where the FTS vector index is real --
    # see couchbase-memory/vector_index.json, which must be kept in sync with this value).
    # 4096 matches qwen3:8b's hidden size; if you change the model, re-check this.
    memory_embedding_dims: int = 4096

    # --- Local LLM: Qwen 3, 8B params, served via Ollama-compatible API ---
    qwen_base_url: str = "http://qwen-service:11434"
    qwen_model_name: str = "qwen3:8b"
    qwen_embedding_model_name: str = "qwen3:8b"
    qwen_request_timeout_s: int = 120

    # --- Capella Management API ---
    capella_api_base_url: str = "https://cloudapi.cloud.couchbase.com/v4"
    capella_api_token: str = ""
    capella_org_id: str = ""

    # --- Migration engine ---
    default_parallelism: int = 4
    migration_state_file: str = "/data/state/migrations.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
