"""OAuth 2.1 authorization server for a single-user MCP server.

Codes and tokens are the SDK's own Pydantic models rather than bespoke classes.
That matters: the SDK reads fields off these objects, and it grows new ones
(``subject`` and ``claims`` arrived in mcp 1.29 and are read on every
authenticated request). A hand-rolled stand-in silently lacks whatever was
added last and fails at request time, not at import.

Tokens live in memory, so a restart requires re-authorization.
"""

from __future__ import annotations

import secrets
import time
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

ACCESS_TOKEN_TTL = 86400  # 24 hours
REFRESH_TOKEN_TTL = 604800  # 7 days
AUTH_CODE_TTL = 300  # 5 minutes

# There is exactly one principal: the person holding MCP_AUTH_PASSWORD. Naming
# it lets the SDK compare the principal that created a session against the one
# making each request, instead of degrading to a client_id-only comparison.
OWNER_SUBJECT = "owner"


class PersonalOAuthProvider:
    """Implements the SDK's OAuthAuthorizationServerProvider protocol.

    Supports Dynamic Client Registration so Claude can auto-register.
    Authorization requires entering a password in the browser, once.
    """

    def __init__(self, base_url: str, auth_password: str):
        self.base_url = base_url.rstrip("/")
        self.auth_password = auth_password
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        # Pending authorization requests, keyed by state.
        self._pending_auth: dict[str, dict] = {}

    # --- Client management ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    # --- Authorization ---

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Park the request and send the browser to the password page."""
        state = params.state or secrets.token_urlsafe(16)

        self._pending_auth[state] = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "scopes": params.scopes or [],
            "resource": params.resource,
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "state": params.state,
        }

        return f"{self.base_url}/oauth/approve?state={state}"

    # --- Authorization code ---

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self._auth_codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        if time.time() > code.expires_at:
            self._auth_codes.pop(authorization_code, None)
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        self._auth_codes.pop(authorization_code.code, None)
        return self._issue(
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )

    # --- Token validation ---

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = self._access_tokens.get(token)
        if access is None:
            return None
        if access.expires_at is not None and time.time() > access.expires_at:
            self._access_tokens.pop(token, None)
            return None
        return access

    # --- Refresh ---

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        token = self._refresh_tokens.get(refresh_token)
        if token is None or token.client_id != client.client_id:
            return None
        if token.expires_at is not None and time.time() > token.expires_at:
            self._refresh_tokens.pop(refresh_token, None)
            return None
        return token

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str] | None = None,
    ) -> OAuthToken:
        # Rotate: the presented refresh token is single-use.
        self._refresh_tokens.pop(refresh_token.token, None)
        return self._issue(
            client_id=client.client_id,
            scopes=scopes or refresh_token.scopes,
            resource=None,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        value = getattr(token, "token", str(token))
        self._access_tokens.pop(value, None)
        self._refresh_tokens.pop(value, None)

    # --- Issuing ---

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        access_value = secrets.token_urlsafe(48)
        refresh_value = secrets.token_urlsafe(48)
        now = int(time.time())

        self._access_tokens[access_value] = AccessToken(
            token=access_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + ACCESS_TOKEN_TTL,
            resource=resource,
            subject=OWNER_SUBJECT,
            claims={"iss": self.base_url, "sub": OWNER_SUBJECT},
        )
        self._refresh_tokens[refresh_value] = RefreshToken(
            token=refresh_value,
            client_id=client_id,
            scopes=scopes,
            expires_at=now + REFRESH_TOKEN_TTL,
            subject=OWNER_SUBJECT,
        )

        return OAuthToken(
            access_token=access_value,
            token_type="bearer",
            refresh_token=refresh_value,
            expires_in=ACCESS_TOKEN_TTL,
        )

    # --- Approval, called from the /oauth/approve route ---

    def has_pending_auth(self, state: str) -> bool:
        return state in self._pending_auth

    def verify_and_approve(self, state: str, password: str) -> str | None:
        """Check the password and return the redirect URL carrying the code."""
        if not secrets.compare_digest(password, self.auth_password):
            return None

        pending = self._pending_auth.pop(state, None)
        if pending is None:
            return None

        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = AuthorizationCode(
            code=code,
            client_id=pending["client_id"],
            redirect_uri=pending["redirect_uri"],
            redirect_uri_provided_explicitly=pending["redirect_uri_provided_explicitly"],
            code_challenge=pending["code_challenge"],
            scopes=pending["scopes"],
            expires_at=time.time() + AUTH_CODE_TTL,
            resource=pending["resource"],
            subject=OWNER_SUBJECT,
        )

        params = {"code": code}
        if pending["state"]:
            params["state"] = pending["state"]
        return f"{pending['redirect_uri']}?{urlencode(params)}"
