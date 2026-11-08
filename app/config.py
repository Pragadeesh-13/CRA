import os
from typing import List
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

def parse_allowed_origins() -> List[str]:
    raw = os.getenv("ALLOWED_ORIGINS", "").strip()
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


class Settings(BaseModel):
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "3000")))
    auth_mode: str = Field(default_factory=lambda: os.getenv("AUTH_MODE", "optional").lower())
    llm_provider: str = Field(default_factory=lambda: os.getenv("LLM_PROVIDER", "lmstudio").lower())
    llm_model: str = Field(default_factory=lambda: os.getenv("LLM_MODEL", "qwen/qwen3.5-9b"))
    lmstudio_base_url: str = Field(
        default_factory=lambda: os.getenv("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
    )
    llm_api_key: str = Field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    llm_stream: bool = Field(
        default_factory=lambda: os.getenv("LLM_STREAM", "true").lower() in ("true", "1", "yes")
    )
    llm_think: str = Field(default_factory=lambda: os.getenv("LLM_THINK", "auto"))
    show_reasoning: bool = Field(
        default_factory=lambda: os.getenv("SHOW_REASONING", os.getenv("LLM_SHOW_REASONING", "false")).lower() in ("true", "1", "yes")
    )
    public_url: str = Field(default_factory=lambda: os.getenv("PUBLIC_URL", "http://localhost:3000"))
    trust_proxy_tls: bool = Field(
        default_factory=lambda: os.getenv("TRUST_PROXY_TLS", "false").lower() in ("true", "1", "yes")
    )
    allowed_origins: List[str] = Field(default_factory=parse_allowed_origins)
    llm_timeout_ms: int = Field(default_factory=lambda: int(os.getenv("LLM_TIMEOUT_MS", "60000")))
    llm_stream_idle_timeout_ms: int = Field(
        default_factory=lambda: int(os.getenv("LLM_STREAM_IDLE_TIMEOUT_MS", "90000"))
    )
    key_encryption_secret: str = Field(
        default_factory=lambda: os.getenv(
            "KEY_ENCRYPTION_SECRET", "6c407159a54cc53fe814dda34fd67f8dc74dedb0830c0d99a5a539b73e524eb6"
        )
    )
    firebase_project_id: str = Field(default_factory=lambda: os.getenv("FIREBASE_PROJECT_ID", ""))
    firebase_web_api_key: str = Field(
        default_factory=lambda: os.getenv("FIREBASE_WEB_API_KEY", "")
    )
    # Public Firebase *web* app config, surfaced to the browser via
    # GET /api/auth/firebase-config. Empty by default; no project values are
    # hardcoded. These are public client values, never server credentials.
    firebase_auth_domain: str = Field(default_factory=lambda: os.getenv("FIREBASE_AUTH_DOMAIN", ""))
    firebase_storage_bucket: str = Field(
        default_factory=lambda: os.getenv("FIREBASE_STORAGE_BUCKET", "")
    )
    firebase_messaging_sender_id: str = Field(
        default_factory=lambda: os.getenv("FIREBASE_MESSAGING_SENDER_ID", "")
    )
    firebase_app_id: str = Field(default_factory=lambda: os.getenv("FIREBASE_APP_ID", ""))
    firebase_measurement_id: str = Field(
        default_factory=lambda: os.getenv("FIREBASE_MEASUREMENT_ID", "")
    )
    # Server-side Firebase Admin credentials reference. Never commit a value;
    # set FIREBASE_SERVICE_ACCOUNT to a service-account JSON path (or inline
    # JSON), or rely on Application Default Credentials.
    firebase_service_account: str = Field(
        default_factory=lambda: os.getenv("FIREBASE_SERVICE_ACCOUNT", "")
    )
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "info"))
    # Admin-configured CRA (PRODUCT_VARIANT.md). Runtime-only, gitignored.
    admin_config_dir: str = Field(
        default_factory=lambda: os.getenv("CRA_ADMIN_CONFIG_DIR", "")
    )
    admin_token: str = Field(default_factory=lambda: os.getenv("CRA_ADMIN_TOKEN", ""))


settings = Settings()
