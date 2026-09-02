"""Guards the OAuth provider against SDK drift.

Production broke because the provider handed the SDK hand-rolled objects while
the SDK had grown a field it reads on every authenticated request. The first
test is the reason this file exists; it is what an `mcp` bump has to keep green.
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
    assert f"state={state}" in redirect
    return redirect.split("code=")[1].split("&")[0]


def issue(provider, client):
    code = approve(provider, client)
    loaded = run(provider.load_authorization_code(client, code))
    assert isinstance(loaded, AuthorizationCode)
    return run(provider.exchange_authorization_code(client, loaded))


def test_the_sdk_gets_its_own_models_back(provider, client):
    token = issue(provider, client)
    stored = run(provider.load_access_token(token.access_token))
    assert isinstance(stored, AccessToken)
    assert isinstance(run(provider.load_refresh_token(client, token.refresh_token)), RefreshToken)

    # The exact call that raised AttributeError: 'Token' has no attribute 'claims'.
    context = authorization_context(AuthenticatedUser(stored))
    assert context["client_id"] == "client-1"
    assert context["issuer"] == BASE_URL
    assert context["subject"] == OWNER_SUBJECT


def test_a_credential_is_never_usable_twice_or_by_anyone_else(provider, client):
    code = approve(provider, client)
    other = client.model_copy(update={"client_id": "client-2"})
    assert run(provider.load_authorization_code(other, code)) is None

    loaded = run(provider.load_authorization_code(client, code))
    token = run(provider.exchange_authorization_code(client, loaded))
    assert run(provider.load_authorization_code(client, code)) is None

    old = run(provider.load_refresh_token(client, token.refresh_token))
    rotated = run(provider.exchange_refresh_token(client, old, None))
    assert rotated.refresh_token != token.refresh_token
    assert run(provider.load_refresh_token(client, token.refresh_token)) is None

    provider._access_tokens[rotated.access_token].expires_at = int(time.time()) - 1
    assert run(provider.load_access_token(rotated.access_token)) is None


def test_a_wrong_password_issues_nothing_but_allows_a_retry(provider, client):
    params = AuthorizationParams(
        state="xyz", scopes=[], code_challenge="c",
        redirect_uri=AnyUrl(REDIRECT), redirect_uri_provided_explicitly=True, resource=None,
    )
    run(provider.authorize(client, params))
    assert provider.verify_and_approve("xyz", "wrong") is None
    assert provider.has_pending_auth("xyz")

    assert not provider.has_pending_auth("nope")
    assert provider.verify_and_approve("nope", PASSWORD) is None
