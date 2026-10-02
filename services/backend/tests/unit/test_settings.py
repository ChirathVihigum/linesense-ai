from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db.session import _ensure_supabase_sslmode, engine_connect_args
from app.settings import Settings, normalize_database_url


def _prod_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "environment": "production",
        "session_secret": "a-very-long-and-safe-session-secret-0123456789",
        "service_token": "a-very-long-and-safe-service-token-0123456789",
        "public_origin": "https://app.linesense.example.com",
        "llm_provider": "anthropic",
        "anthropic_api_key": "sk-live-example-key",
    }
    kwargs.update(overrides)
    return kwargs


def test_production_rejects_dev_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            **_prod_kwargs(session_secret="dev-session-secret-change-me-0123456789abcdef"),
        )


def test_production_rejects_http_origin() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_prod_kwargs(public_origin="http://app.linesense.example.com"))


def test_production_rejects_fixture_llm_provider() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **_prod_kwargs(llm_provider="fixture"))


def test_development_defaults_load() -> None:
    settings = Settings(_env_file=None)
    assert settings.environment == "development"
    assert settings.llm_provider == "fixture"
    assert settings.database_url.endswith("/linesense_dev")
    assert settings.session_max_age_seconds == 28800
    assert settings.run_deadline_seconds == 120
    assert settings.max_upload_bytes == 10_485_760


def test_supabase_database_url_normalizes_and_configures_pooler() -> None:
    raw_url = (
        "postgresql://postgres.myref:secret@aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
    )
    settings = Settings(_env_file=None, database_url=raw_url)
    assert settings.database_url.startswith("postgresql+psycopg://")
    # Migration URL automatically follows database_url when not overridden
    assert settings.migration_database_url == settings.database_url
    assert engine_connect_args(settings.database_url) == {"prepare_threshold": None}
    assert "sslmode=require" in _ensure_supabase_sslmode(settings.database_url)
    assert normalize_database_url("postgres://u:p@db.ref.supabase.co:5432/postgres").startswith(
        "postgresql+psycopg://"
    )
