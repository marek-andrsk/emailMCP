import hashlib
import base64
import secrets
import time
import os
from urllib.parse import urlencode

from fastmcp.server.auth.auth import OAuthProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from mcp.server.auth.settings import ClientRegistrationOptions


# Simple typed dicts for token storage
class AuthCode:
    def __init__(
        self,
        code,
        client_id,
        redirect_uri,
        code_challenge,
        code_challenge_method,
        scopes,
        expires_at,
        redirect_uri_provided_explicitly,
    ):
        self.code = code
        self.client_id = client_id
        self.redirect_uri = redirect_uri
        self.code_challenge = code_challenge
        self.code_challenge_method = code_challenge_method
        self.scopes = scopes
        self.expires_at = expires_at
        self.redirect_uri_provided_explicitly = redirect_uri_provided_explicitly


class Token:
    def __init__(self, token, client_id, scopes, expires_at):
        self.token = token
        self.client_id = client_id
        self.scopes = scopes
        self.expires_at = expires_at


class PersonalOAuthProvider(OAuthProvider):
    """Simple OAuth provider for a single-user MCP server.

    Supports Dynamic Client Registration (DCR) so Claude can auto-register.
    Authorization requires entering a password in the browser (one-time).
    Tokens are stored in memory — server restart requires re-auth.
    """

    def __init__(self, base_url: str, auth_password: str):
        super().__init__(
            base_url=base_url,
            issuer_url=base_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self.auth_password = auth_password
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthCode] = {}
        self._access_tokens: dict[str, Token] = {}
        self._refresh_tokens: dict[str, Token] = {}
        # Pending authorization requests (state -> params)
        self._pending_auth: dict[str, dict] = {}

    # --- Client Management ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self._clients[client_info.client_id] = client_info

    # --- Authorization ---

    async def authorize(self, client, params) -> str:
        """Store the auth request and show a password page."""
        state = params.state or secrets.token_urlsafe(16)

        code_challenge_method = getattr(params, "code_challenge_method", None)
        self._pending_auth[state] = {
            "client_id": client.client_id,
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "code_challenge_method": code_challenge_method or "S256",
            "scopes": params.scopes or [],
            "redirect_uri_provided_explicitly": getattr(
                params, "redirect_uri_provided_explicitly", True
            ),
            "state": state,
        }

        # Redirect to our approval page (strip trailing slash to avoid //)
        base = str(self.base_url).rstrip("/")
        return f"{base}/oauth/approve?state={state}"

    # --- Auth Code ---

    async def load_authorization_code(self, client, authorization_code: str):
        ac = self._auth_codes.get(authorization_code)
        if not ac:
            return None
        if ac.client_id != client.client_id:
            return None
        if time.time() > ac.expires_at:
            self._auth_codes.pop(authorization_code, None)
            return None
        return ac

    async def exchange_authorization_code(self, client, authorization_code) -> OAuthToken:
        access = secrets.token_urlsafe(48)
        refresh = secrets.token_urlsafe(48)
        now = time.time()

        self._access_tokens[access] = Token(
            token=access,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + 86400,  # 24 hours
        )
        self._refresh_tokens[refresh] = Token(
            token=refresh,
            client_id=client.client_id,
            scopes=authorization_code.scopes,
            expires_at=now + 604800,  # 7 days
        )

        # Remove used auth code
        self._auth_codes.pop(authorization_code.code, None)

        return OAuthToken(
            access_token=access,
            token_type="bearer",
            refresh_token=refresh,
            expires_in=86400,
        )

    # --- Token Validation ---

    async def load_access_token(self, token: str):
        t = self._access_tokens.get(token)
        if not t:
            return None
        if time.time() > t.expires_at:
            self._access_tokens.pop(token, None)
            return None
        return t

    # --- Refresh ---

    async def load_refresh_token(self, client, refresh_token: str):
        rt = self._refresh_tokens.get(refresh_token)
        if not rt:
            return None
        if rt.client_id != client.client_id:
            return None
        if time.time() > rt.expires_at:
            self._refresh_tokens.pop(refresh_token, None)
            return None
        return rt

    async def exchange_refresh_token(self, client, refresh_token, scopes) -> OAuthToken:
        access = secrets.token_urlsafe(48)
        new_refresh = secrets.token_urlsafe(48)
        now = time.time()
        use_scopes = scopes or refresh_token.scopes

        self._access_tokens[access] = Token(
            token=access,
            client_id=client.client_id,
            scopes=use_scopes,
            expires_at=now + 86400,
        )
        self._refresh_tokens[new_refresh] = Token(
            token=new_refresh,
            client_id=client.client_id,
            scopes=use_scopes,
            expires_at=now + 604800,
        )

        # Rotate: remove old
        self._refresh_tokens.pop(refresh_token.token, None)

        return OAuthToken(
            access_token=access,
            token_type="bearer",
            refresh_token=new_refresh,
            expires_in=86400,
        )

    async def revoke_token(self, token) -> None:
        tok = getattr(token, "token", str(token))
        self._access_tokens.pop(tok, None)
        self._refresh_tokens.pop(tok, None)

    # --- Approval Logic (called from custom route in server.py) ---

    def verify_and_approve(self, state: str, password: str) -> str | None:
        """Verify password and return redirect URL with auth code, or None if invalid."""
        if password != self.auth_password:
            return None

        pending = self._pending_auth.pop(state, None)
        if not pending:
            return None

        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = AuthCode(
            code=code,
            client_id=pending["client_id"],
            redirect_uri=pending["redirect_uri"],
            code_challenge=pending["code_challenge"],
            code_challenge_method=pending["code_challenge_method"],
            scopes=pending["scopes"],
            expires_at=time.time() + 300,  # 5 min
            redirect_uri_provided_explicitly=pending[
                "redirect_uri_provided_explicitly"
            ],
        )

        params = {"code": code}
        if pending["state"]:
            params["state"] = pending["state"]

        return f"{pending['redirect_uri']}?{urlencode(params)}"

    def has_pending_auth(self, state: str) -> bool:
        return state in self._pending_auth
