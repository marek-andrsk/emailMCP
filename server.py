import os
import html

from dotenv import load_dotenv

load_dotenv()

from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from imap_client import IMAPClient
from oauth_provider import PersonalOAuthProvider

# --- Auth ---
base_url = os.environ.get("BASE_URL", "http://localhost:8000")
auth_password = os.environ["MCP_AUTH_PASSWORD"]
oauth = PersonalOAuthProvider(base_url=base_url, auth_password=auth_password)

# --- MCP Server ---
allowed_hosts = [
    host.strip()
    for host in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
    if host.strip()
]
transport_security = None
if allowed_hosts:
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
    )

resource_url = f"{base_url.rstrip('/')}/mcp"
auth_settings = AuthSettings(
    issuer_url=base_url,
    resource_server_url=resource_url,
    client_registration_options=ClientRegistrationOptions(enabled=True),
    revocation_options=RevocationOptions(enabled=False),
)
mcp = FastMCP(
    "Email MCP",
    auth_server_provider=oauth,
    auth=auth_settings,
    transport_security=transport_security,
)
imap = IMAPClient()


@mcp.tool(
    description=(
        "List inbox threads (latest message per thread). Returns metadata only: uid, from, subject, date, snippet, "
        "thread_uids, needs_reply. "
        "After calling this, present threads to the user ONE AT A TIME using read_email on the latest uid. "
        "For each email: summarize it, then ask the user if they want to reply, skip, or stop reviewing. "
        "Do NOT read all emails at once."
    )
)
def list_inbox() -> list[dict]:
    return imap.list_inbox()


@mcp.tool(
    description=(
        "Read the full content of a single email by its UID. Thread-aware. "
        "Returns from, to, cc, subject, date, body, message_id, references, thread_uids, thread_context, needs_reply. "
        "Use this to show the user a summary of the email content."
    )
)
def read_email(uid: int) -> dict:
    return imap.read_email(uid)


@mcp.tool(
    description=(
        "Save a reply draft to the IMAP Drafts folder. This does NOT send the email. "
        "The user will review and send it manually from their email client. "
        "Provide the UID of the email being replied to and the plain text reply body. "
        "The draft will have correct In-Reply-To and References headers for proper threading."
    )
)
def save_draft(reply_to_uid: int, body: str) -> str:
    return imap.save_draft(reply_to_uid, body)


# --- MCP Prompt ---
@mcp.prompt(
    description="Review inbox emails one by one, with option to draft replies"
)
def review_inbox() -> str:
    return (
        "Check my inbox and walk me through emails one by one. "
        "For each email: summarize it (keep long emails concise), then ask if I want to reply or skip. "
        "If I want to reply, I'll give you context about what to say and you'll draft the reply "
        "and save it to my Drafts folder. Never send emails directly."
    )


# --- OAuth approval page ---
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


@mcp.custom_route("/oauth/approve", methods=["GET", "POST"])
async def oauth_approve(request: Request):
    if request.method == "GET":
        state = request.query_params.get("state", "")
        if not oauth.has_pending_auth(state):
            return HTMLResponse("<p>Invalid or expired authorization request.</p>", status_code=400)
        safe_state = html.escape(state, quote=True)
        html_body = APPROVE_HTML.format(state=safe_state, error="")
        return HTMLResponse(html_body)

    # POST — verify password
    form = await request.form()
    state = form.get("state", "")
    password = form.get("password", "")

    if not oauth.has_pending_auth(state):
        return HTMLResponse("<p>Invalid or expired authorization request.</p>", status_code=400)

    redirect_url = oauth.verify_and_approve(state, password)
    if not redirect_url:
        safe_state = html.escape(state, quote=True)
        html_body = APPROVE_HTML.format(state=safe_state, error='<p class="error">Wrong password.</p>')
        return HTMLResponse(html_body)

    return RedirectResponse(redirect_url, status_code=302)


# --- Health endpoint ---
@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


# --- ASGI app ---
app = mcp.streamable_http_app()
app = CORSMiddleware(
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

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
