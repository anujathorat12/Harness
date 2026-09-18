"""
Central configuration, loaded from environment variables (12-factor style).

Nothing security-relevant should ever be hardcoded elsewhere in the codebase;
if a component needs a limit, timeout, or secret, it should come from here.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    # --- Service identity ---
    service_name: str = "byoa-harness"
    environment: str = "local"  # local | staging | production
    api_version: str = "v1"

    # --- Persistence ---
    # Defaults to a local SQLite file so the platform runs with zero external
    # dependencies. Point this at a Postgres DSN in real deployments, e.g.
    # postgresql+asyncpg://user:pass@host/db -- the SQLAlchemy models are
    # written to be database-agnostic.
    database_url: str = "sqlite+aiosqlite:///./data/harness.db"

    # --- Runtime / sandbox defaults (overridable per-agent at registration) ---
    default_cpu_seconds: int = 5
    default_memory_mb: int = 256
    default_timeout_seconds: int = 30
    default_pid_limit: int = 32
    default_output_bytes_limit: int = 1_000_000
    runtime_workdir_root: str = "/tmp/harness-sandboxes"

    # --- Concurrency ---
    max_concurrent_sessions_per_agent: int = 3
    max_concurrent_sessions_total: int = 50

    # --- Approval ---
    approval_default_timeout_seconds: int = 600  # 10 minutes

    # --- Audit ---
    audit_redacted_keys: tuple[str, ...] = (
        "password", "token", "secret", "api_key", "apikey",
        "authorization", "access_token", "refresh_token", "private_key",
    )

    # --- HTTP ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Authentication / authorization ---
    # Deliberately simple: one static API key per role, sent as the
    # `X-API-Key` header. This is NOT an enterprise IAM system -- no user
    # database, no password hashing, no token expiry -- by design, per the
    # assignment's own "do not overbuild" instruction. What it DOES provide,
    # and what is actually enforced server-side on every route (never just
    # hidden in the frontend): four roles (ADMIN/OPERATOR/AUDITOR/CLIENT)
    # with a fixed permission table. See harness/core/security.py and
    # SECURITY.md. The *** in these defaults makes it visually obvious in
    # logs/diffs that they are placeholders, not real secrets.
    api_key_admin: str = "dev-admin-key-***CHANGE-ME***"
    api_key_operator: str = "dev-operator-key-***CHANGE-ME***"
    api_key_auditor: str = "dev-auditor-key-***CHANGE-ME***"
    api_key_client: str = "dev-client-key-***CHANGE-ME***"

    # --- CORS (needed once a separately-hosted frontend calls this API) ---
    # Kept as a plain string field, not list[str]: pydantic-settings tries to
    # JSON-decode any list-typed env var *before* field validators ever run,
    # so a plain comma-separated value (the natural way to set this in
    # docker-compose.yml) would crash the app at startup with a
    # SettingsError. Splitting it ourselves in a property sidesteps that
    # entirely -- see `cors_allow_origins` below.
    cors_allow_origins_csv: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_allow_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins_csv.split(",") if origin.strip()]

    # --- Container runtime (used only when an agent version declares
    # runtime.kind == "container"; requires a Docker daemon on the host
    # running the harness) ---
    container_runtime_image_pull: bool = True
    container_runtime_network: str = "none"  # sandboxes get no network by default


settings = Settings()
