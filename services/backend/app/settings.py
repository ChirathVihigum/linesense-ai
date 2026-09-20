"""Application settings loaded from environment variables (prefix ``LS_``).

Settings are read from process environment variables first, then from a
``.env`` file at the repository root, then from ``services/backend/.env`` if
present (the latter overrides the former for any key set in both).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_APP_DIR = Path(__file__).resolve().parent
_BACKEND_ROOT = _APP_DIR.parent
_REPO_ROOT = _BACKEND_ROOT.parent.parent

Environment = Literal["development", "test", "production"]
LLMProvider = Literal["anthropic", "fixture", "disabled"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LS_",
        env_file=(str(_REPO_ROOT / ".env"), str(_BACKEND_ROOT / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    environment: Environment = "development"

    database_url: str = (
        "postgresql+psycopg://linesense_app:dev-app-only@127.0.0.1:55432/linesense_dev"
    )
    migration_database_url: str = (
        "postgresql+psycopg://linesense_owner:dev-owner-only@127.0.0.1:55432/linesense_dev"
    )
    test_database_url: str = (
        "postgresql+psycopg://linesense_app:dev-app-only@127.0.0.1:55432/linesense_test"
    )
    test_migration_database_url: str = (
        "postgresql+psycopg://linesense_owner:dev-owner-only@127.0.0.1:55432/linesense_test"
    )
    app_db_role: str = "linesense_app"

    session_secret: SecretStr = SecretStr("dev-session-secret-change-me-0123456789abcdef")
    service_token: SecretStr = SecretStr("dev-service-token-change-me-0123456789")

    public_origin: str = "http://localhost:5173"
    api_internal_url: str = "http://127.0.0.1:8000"

    oidc_issuer: str = "http://127.0.0.1:8090"
    oidc_client_id: str = "linesense-web"
    oidc_client_secret: SecretStr = SecretStr("dev-oidc-client-secret")
    oidc_redirect_uri: str = "http://localhost:5173/auth/callback"

    llm_provider: LLMProvider = "fixture"
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_model: str = "claude-opus-5"

    document_storage_dir: str = "../../.local/documents"
    # Directory for worker liveness files (non-production only); relative
    # paths resolve against services/backend, so the default is the repo's
    # git-ignored `.local/`.
    worker_alive_dir: str = "../../.local"

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedder: str = "fastembed"

    dev_idp_password: SecretStr = SecretStr("demo-password")

    session_max_age_seconds: int = 28800
    run_deadline_seconds: int = 120
    max_upload_bytes: int = 10_485_760

    # task-25-brief.md req. 2: single-process token-bucket rate limiting.
    # `None` (the default) means "on, except when `environment == 'test'`" so
    # ordinary test runs never trip it; `True`/`False` are explicit overrides
    # for a test (or a deployment) that wants a fixed answer either way.
    rate_limit_enabled: bool | None = None

    @model_validator(mode="after")
    def _validate_production_hardening(self) -> Settings:
        if self.environment != "production":
            return self

        errors: list[str] = []
        session_secret = self.session_secret.get_secret_value()
        service_token = self.service_token.get_secret_value()

        if len(session_secret) < 32 or "dev-" in session_secret:
            errors.append(
                "session_secret must be at least 32 characters and must not contain 'dev-' "
                "when LS_ENVIRONMENT=production"
            )
        if len(service_token) < 32 or "dev-" in service_token:
            errors.append(
                "service_token must be at least 32 characters and must not contain 'dev-' "
                "when LS_ENVIRONMENT=production"
            )
        if not self.public_origin.startswith("https://"):
            errors.append("public_origin must use https:// when LS_ENVIRONMENT=production")
        # The development OIDC provider (devtools/dev_oidc, ADR-0004) must never
        # be the identity provider of a production deployment.
        if not self.oidc_issuer.startswith("https://"):
            errors.append("oidc_issuer must use https:// when LS_ENVIRONMENT=production")
        if not self.oidc_redirect_uri.startswith("https://"):
            errors.append("oidc_redirect_uri must use https:// when LS_ENVIRONMENT=production")
        if "dev-" in self.oidc_client_secret.get_secret_value():
            errors.append(
                "oidc_client_secret must not be a development value when LS_ENVIRONMENT=production"
            )
        if self.llm_provider == "fixture":
            errors.append("llm_provider must not be 'fixture' when LS_ENVIRONMENT=production")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key.get_secret_value():
            errors.append(
                "anthropic_api_key is required when llm_provider is 'anthropic' and "
                "LS_ENVIRONMENT=production"
            )

        if errors:
            raise ValueError("; ".join(errors))
        return self


def resolve_backend_path(value: str) -> Path:
    """Resolve ``value`` against ``services/backend`` unless it is absolute."""
    path = Path(value)
    return path if path.is_absolute() else (_BACKEND_ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
