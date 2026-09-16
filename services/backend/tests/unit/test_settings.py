from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.settings import Settings


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
