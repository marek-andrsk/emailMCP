import os

from dotenv import load_dotenv

load_dotenv()

from fastmcp import FastMCP
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from imap_client import IMAPClient
from oauth_provider import PersonalOAuthProvider

# --- Auth ---
base_url = os.environ.get("BASE_URL", "http://localhost:8000")
auth_password = os.environ["MCP_AUTH_PASSWORD"]
oauth = PersonalOAuthProvider(base_url=base_url, auth_password=auth_password)

# --- MCP Server ---
mcp = FastMCP("Email MCP", auth=oauth)
imap = IMAPClient()


@mcp.tool(
    description=(
        "List all emails in the inbox. Returns metadata only: uid, from, subject, date, snippet. "
        "After calling this, present emails to the user ONE AT A TIME using read_email. "
        "For each email: summarize it, then ask the user if they want to reply, skip, or stop reviewing. "
        "Do NOT read all emails at once."
    )
)
def list_inbox() -> list[dict]:
    return imap.list_inbox()


@mcp.tool(
    description=(
        "Read the full content of a single email by its UID. "
        "Returns from, to, cc, subject, date, body, message_id, and references. "
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
        body { font-family: system-ui, sans-serif; max-width: 400px; margin: 80px auto; padding: 0 20px; }
        h1 { font-size: 1.3em; }
        input[type=password] { width: 100%%; padding: 10px; margin: 10px 0; box-sizing: border-box; font-size: 1em; }
        button { padding: 10px 24px; font-size: 1em; cursor: pointer; }
        .error { color: #c00; }
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
        html = APPROVE_HTML.format(state=state, error="")
        return HTMLResponse(html)

    # POST — verify password
    form = await request.form()
    state = form.get("state", "")
    password = form.get("password", "")

    if not oauth.has_pending_auth(state):
        return HTMLResponse("<p>Invalid or expired authorization request.</p>", status_code=400)

    redirect_url = oauth.verify_and_approve(state, password)
    if not redirect_url:
        html = APPROVE_HTML.format(state=state, error='<p class="error">Wrong password.</p>')
        return HTMLResponse(html)

    return RedirectResponse(redirect_url, status_code=302)


# --- Health endpoint ---
@mcp.custom_route("/health", methods=["GET"])
async def health(request):
    return JSONResponse({"status": "ok"})


# --- ASGI app ---
middleware = [
    Middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=[
            "mcp-protocol-version",
            "mcp-session-id",
            "Authorization",
            "Content-Type",
        ],
        expose_headers=["mcp-session-id"],
    ),
]

app = mcp.http_app(middleware=middleware)

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
