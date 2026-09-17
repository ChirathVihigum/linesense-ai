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


class OidcConfigurationError(RuntimeError):
    """The provider's discovery document does not match ``LS_OIDC_ISSUER``."""


def normalize_issuer(issuer: str) -> str:
    """Issuer identity used for ``users.issuer`` and all comparisons.

    ``LS_OIDC_ISSUER`` and the discovery document's ``issuer`` may differ only
    by a trailing slash; ``users`` rows (callback, seed and test helpers) always
    store the normalized value.
    """
    return issuer.rstrip("/")


def build_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name=CLIENT_NAME,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        server_metadata_url=normalize_issuer(settings.oidc_issuer) + DISCOVERY_PATH,
        client_kwargs={
            "scope": "openid email profile",
            "code_challenge_method": "S256",
            "timeout": 10.0,
        },
    )
    return oauth


async def discovered_issuer(client: Any, settings: Settings) -> str:
    """The provider's ``issuer`` (exactly as published), after checking it names
    the configured ``LS_OIDC_ISSUER``."""
    metadata = await client.load_server_metadata()
    issuer = metadata.get("issuer")
    if not isinstance(issuer, str) or normalize_issuer(issuer) != normalize_issuer(
        settings.oidc_issuer
    ):
        raise OidcConfigurationError(
            "The OIDC discovery document's issuer does not match LS_OIDC_ISSUER."
        )
    return issuer


def id_token_claims_options(issuer: str, client_id: str) -> dict[str, dict[str, Any]]:
    """Claim requirements for ID tokens.

    Supplying options replaces Authlib's default ``iss`` check, so ``iss`` is
    listed explicitly; ``aud`` must be enforced here because Authlib's own
    ``azp`` check accepts a foreign audience when ``azp`` names this client.
    ``exp``/``iat`` and ``nonce`` are always validated by Authlib/joserfc.
    """
    return {
        "iss": {"essential": True, "value": issuer},
        "aud": {"essential": True, "value": client_id},
        "sub": {"essential": True},
    }


def oidc_client(request: Request) -> Any:
    """The registered Authlib ``StarletteOAuth2App`` for this application."""
    return request.app.state.oauth.create_client(CLIENT_NAME)
