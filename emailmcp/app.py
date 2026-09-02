"""Wiring: configuration → registry → FastMCP server → ASGI app."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv

load_dotenv()

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import prompts, tools
from .auth import PersonalOAuthProvider
from .auth import routes as auth_routes
from .config import load_mailboxes
from .registry import Registry

SERVER_NAME = "Email MCP"


def _transport_security() -> TransportSecuritySettings | None:
    allowed_hosts = [
        host.strip() for host in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",") if host.strip()
    ]
    if not allowed_hosts:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
    )


def create_server() -> tuple[FastMCP, Registry]:
    base_url = os.environ.get("BASE_URL", "http://localhost:8000")
    oauth = PersonalOAuthProvider(
        base_url=base_url,
        auth_password=os.environ["MCP_AUTH_PASSWORD"],
    )

    auth_settings = AuthSettings(
        issuer_url=base_url,
        resource_server_url=f"{base_url.rstrip('/')}/mcp",
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=False),
    )

    mcp = FastMCP(
        SERVER_NAME,
        auth_server_provider=oauth,
        auth=auth_settings,
        transport_security=_transport_security(),
    )

    # Fails fast with every configuration problem at once.
    registry = Registry(load_mailboxes())

    tools.register(mcp, registry)
    prompts.register(mcp, registry)
    auth_routes.register(mcp, oauth)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(request: Request):
        return JSONResponse({"status": "ok", "mailboxes": registry.keys})

    return mcp, registry


def create_app():
    mcp, registry = create_server()
    app = mcp.streamable_http_app()
    _close_registry_on_shutdown(app, registry)
    return CORSMiddleware(
        app,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "mcp-protocol-version",
            "mcp-session-id",
            "Authorization",
            "Content-Type",
        ],
        expose_headers=["mcp-session-id"],
    )


def _close_registry_on_shutdown(app, registry: Registry) -> None:
    """Release the IMAP connections when the process stops.

    FastMCP's own ``lifespan`` hook runs per MCP session, which is the wrong
    scope for a connection pool shared by every session, so this composes onto
    the ASGI app's lifespan instead. Starlette ignores ``on_shutdown`` handlers
    once a lifespan is set, and the session manager already sets one.
    """
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(scope):
        async with inner(scope):
            try:
                yield
            finally:
                registry.close()

    app.router.lifespan_context = lifespan
