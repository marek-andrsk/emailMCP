"""The password-gated approval page that completes the authorization flow."""

from __future__ import annotations

import html

from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse

from .provider import PersonalOAuthProvider

APPROVE_HTML = """<!DOCTYPE html>
<html>
<head>
    <title>Authorize Email MCP</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ font-family: system-ui, sans-serif; max-width: 400px; margin: 80px auto; padding: 0 20px; }}
        h1 {{ font-size: 1.3em; }}
        input[type=password] {{ width: 100%%; padding: 10px; margin: 10px 0; box-sizing: border-box; font-size: 1em; }}
        button {{ padding: 10px 24px; font-size: 1em; cursor: pointer; }}
        .error {{ color: #c00; }}
    </style>
</head>
<body>
    <h1>Authorize Claude to access your email?</h1>
    <p>Enter your MCP password to approve this connection.</p>
    {error}
    <form method="POST">
        <input type="hidden" name="state" value="{state}">
        <input type="password" name="password" placeholder="Password" autofocus>
        <button type="submit">Approve</button>
    </form>
</body>
</html>"""

INVALID_REQUEST = "<p>Invalid or expired authorization request.</p>"


def register(mcp: FastMCP, oauth: PersonalOAuthProvider) -> None:
    @mcp.custom_route("/oauth/approve", methods=["GET", "POST"])
    async def oauth_approve(request: Request):
        if request.method == "GET":
            state = request.query_params.get("state", "")
            if not oauth.has_pending_auth(state):
                return HTMLResponse(INVALID_REQUEST, status_code=400)
            return HTMLResponse(
                APPROVE_HTML.format(state=html.escape(state, quote=True), error="")
            )

        form = await request.form()
        state = form.get("state", "")
        password = form.get("password", "")

        if not oauth.has_pending_auth(state):
            return HTMLResponse(INVALID_REQUEST, status_code=400)

        redirect_url = oauth.verify_and_approve(state, password)
        if not redirect_url:
            return HTMLResponse(
                APPROVE_HTML.format(
                    state=html.escape(state, quote=True),
                    error='<p class="error">Wrong password.</p>',
                )
            )

        return RedirectResponse(redirect_url, status_code=302)
