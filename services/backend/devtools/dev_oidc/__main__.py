"""``python -m devtools.dev_oidc`` — run the development OIDC provider.

Refuses to start (exit code 2) unless ``LS_ENVIRONMENT`` is ``development``
or ``test``. Listens on the host/port of ``LS_OIDC_ISSUER`` (default
``http://127.0.0.1:8090``).
"""

from __future__ import annotations

import sys
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings

from app.settings import Settings

_ALLOWED_ENVIRONMENTS = ("development", "test")


class _EnvironmentOnly(BaseSettings):
    """Reads ``LS_ENVIRONMENT`` from the same sources as ``Settings`` without
    running its production validator (which would fail first, with a less
    useful message, on development secrets)."""

    model_config = Settings.model_config
    environment: str = "development"


def main() -> int:
    environment = _EnvironmentOnly().environment
    if environment not in _ALLOWED_ENVIRONMENTS:
        print(
            f"Refusing to start the development identity provider: LS_ENVIRONMENT is "
            f"{environment!r}; it may only run when LS_ENVIRONMENT is 'development' or 'test'.",
            file=sys.stderr,
        )
        return 2

    import uvicorn

    from devtools.dev_oidc.app import DevIdpConfig, create_dev_idp_app, load_users

    settings = Settings()
    issuer = urlsplit(settings.oidc_issuer)
    host = issuer.hostname or "127.0.0.1"
    port = issuer.port or 8090
    app = create_dev_idp_app(
        DevIdpConfig(
            issuer=settings.oidc_issuer,
            client_id=settings.oidc_client_id,
            client_secret=settings.oidc_client_secret.get_secret_value(),
            redirect_uri=settings.oidc_redirect_uri,
            password=settings.dev_idp_password.get_secret_value(),
            users=load_users(),
        )
    )
    print(
        f"Development identity provider (not for production) at {settings.oidc_issuer}", flush=True
    )
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
