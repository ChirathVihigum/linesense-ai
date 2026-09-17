"""The OIDC relying-party client (Authlib).

One ``OAuth`` registry is built per application instance (``app.state.oauth``)
because Authlib caches the provider's discovery document and JWKS on the
registered client.
"""

from __future__ import annotations

from typing import Any

from authlib.integrations.starlette_client import OAuth
from starlette.requests import Request

from app.settings import Settings

CLIENT_NAME = "linesense"
DISCOVERY_PATH = "/.well-known/openid-configuration"


def build_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name=CLIENT_NAME,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        server_metadata_url=settings.oidc_issuer.rstrip("/") + DISCOVERY_PATH,
        client_kwargs={
            "scope": "openid email profile",
            "code_challenge_method": "S256",
            "timeout": 10.0,
        },
    )
    return oauth


def oidc_client(request: Request) -> Any:
    """The registered Authlib ``StarletteOAuth2App`` for this application."""
    return request.app.state.oauth.create_client(CLIENT_NAME)
