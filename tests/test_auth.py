"""Guards the OAuth provider against SDK drift.

Production broke because the provider handed the SDK hand-rolled objects while
the SDK had grown a field it reads on every authenticated request. These tests
assert we return the SDK's own models and that its helpers accept them.
"""

import asyncio
import time

import pytest
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser, authorization_context
from mcp.server.auth.provider import AccessToken, AuthorizationCode, AuthorizationParams, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from emailmcp.auth.provider import OWNER_SUBJECT, PersonalOAuthProvider

BASE_URL = "https://mcp.example.com"
PASSWORD = "correct-horse"
REDIRECT = "http://localhost:9999/callback"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def provider():
    return PersonalOAuthProvider(base_url=BASE_URL, auth_password=PASSWORD)


@pytest.fixture
def client():
    return OAuthClientInformationFull(
        client_id="client-1",
        redirect_uris=[AnyUrl(REDIRECT)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


def approve(provider, client, state="xyz"):
    """Drive authorize -> approve and return the issued authorization code."""
    params = AuthorizationParams(
        state=state,
        scopes=[],
        code_challenge="challenge",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
        resource=None,
    )
    run(provider.authorize(client, params))
    redirect = provider.verify_and_approve(state, PASSWORD)
    assert redirect is not None
    return redirect.split("code=")[1].split("&")[0]


def issue(provider, client):
    code = approve(provider, client)
    loaded = run(provider.load_authorization_code(client, code))
    return run(provider.exchange_authorization_code(client, loaded))


# --- SDK model contract ----------------------------------------------------


def test_access_token_is_the_sdk_model(provider, client):
    token = issue(provider, client)
    stored = run(provider.load_access_token(token.access_token))
    assert isinstance(stored, AccessToken)


def test_refresh_token_is_the_sdk_model(provider, client):
    token = issue(provider, client)
    stored = run(provider.load_refresh_token(client, token.refresh_token))
    assert isinstance(stored, RefreshToken)


def test_authorization_code_is_the_sdk_model(provider, client):
    code = approve(provider, client)
    assert isinstance(run(provider.load_authorization_code(client, code)), AuthorizationCode)


def test_the_sdk_can_build_an_authorization_context(provider, client):
    """The exact call that raised AttributeError: 'Token' has no attribute 'claims'."""
    token = issue(provider, client)
    stored = run(provider.load_access_token(token.access_token))
    context = authorization_context(AuthenticatedUser(stored))
    assert context["client_id"] == "client-1"
    assert context["issuer"] == BASE_URL
    assert context["subject"] == OWNER_SUBJECT


def test_the_principal_is_named_rather_than_anonymous(provider, client):
    token = issue(provider, client)
    stored = run(provider.load_access_token(token.access_token))
    assert stored.subject == OWNER_SUBJECT
    assert stored.claims["iss"] == BASE_URL


# --- Flow ------------------------------------------------------------------


def test_a_wrong_password_issues_no_code(provider, client):
    params = AuthorizationParams(
        state="xyz", scopes=[], code_challenge="c",
        redirect_uri=AnyUrl(REDIRECT), redirect_uri_provided_explicitly=True, resource=None,
    )
    run(provider.authorize(client, params))
    assert provider.verify_and_approve("xyz", "wrong") is None
    # The pending request survives, so the user can retry.
    assert provider.has_pending_auth("xyz")


def test_an_unknown_state_is_rejected(provider):
    assert not provider.has_pending_auth("nope")
    assert provider.verify_and_approve("nope", PASSWORD) is None


def test_an_authorization_code_is_single_use(provider, client):
    code = approve(provider, client)
    loaded = run(provider.load_authorization_code(client, code))
    run(provider.exchange_authorization_code(client, loaded))
    assert run(provider.load_authorization_code(client, code)) is None


def test_another_client_cannot_use_the_code(provider, client):
    code = approve(provider, client)
    other = client.model_copy(update={"client_id": "client-2"})
    assert run(provider.load_authorization_code(other, code)) is None


def test_refresh_rotates_and_retires_the_old_token(provider, client):
    token = issue(provider, client)
    old = run(provider.load_refresh_token(client, token.refresh_token))
    rotated = run(provider.exchange_refresh_token(client, old, None))
    assert rotated.refresh_token != token.refresh_token
    assert run(provider.load_refresh_token(client, token.refresh_token)) is None


def test_an_expired_access_token_is_refused(provider, client):
    token = issue(provider, client)
    provider._access_tokens[token.access_token].expires_at = int(time.time()) - 1
    assert run(provider.load_access_token(token.access_token)) is None


def test_revocation_drops_both_kinds(provider, client):
    token = issue(provider, client)
    access = run(provider.load_access_token(token.access_token))
    refresh = run(provider.load_refresh_token(client, token.refresh_token))
    run(provider.revoke_token(access))
    run(provider.revoke_token(refresh))
    assert run(provider.load_access_token(token.access_token)) is None
    assert run(provider.load_refresh_token(client, token.refresh_token)) is None


def test_the_client_state_is_echoed_back(provider, client):
    params = AuthorizationParams(
        state="client-state", scopes=[], code_challenge="c",
        redirect_uri=AnyUrl(REDIRECT), redirect_uri_provided_explicitly=True, resource=None,
    )
    run(provider.authorize(client, params))
    assert "state=client-state" in provider.verify_and_approve("client-state", PASSWORD)
