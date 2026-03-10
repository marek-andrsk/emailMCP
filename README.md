# Email MCP Server

Personal MCP server that gives Claude read access to your IMAP inbox and the ability to save reply drafts. No send capability by design.

## Tools

| Tool | Description |
|---|---|
| `list_inbox` | List all emails in INBOX (metadata only: uid, from, subject, date, snippet) |
| `read_email` | Read full email content by UID |
| `save_draft` | Save a reply draft to IMAP Drafts folder (does NOT send) |

## Setup

### 1. Environment variables

Copy `.env.example` to `.env` and fill in your values:

```
IMAP_HOST=mail.example.com
IMAP_PORT=993
IMAP_USER=user@example.com
IMAP_PASS=your-imap-password
MCP_AUTH_PASSWORD=pick-a-strong-password-for-oauth-approval
BASE_URL=https://your-mcp-server.example.com
PORT=8000
```

### 2. Run locally

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py
```

Server starts at `http://localhost:8000`. Health check at `/health`.

### 3. Deploy

The project includes a `Procfile` so it works out of the box with platforms like Railway, Render, Coolify, Heroku, or any Nixpacks/Buildpack-based host. Just push to your git repo and set the env vars.

For Docker-based deployment, any image that installs `requirements.txt` and runs `uvicorn server:app --host 0.0.0.0 --port $PORT` will work.

`BASE_URL` must match your public domain (e.g. `https://email-mcp.yourdomain.com`). HTTPS is required for OAuth.

## Connect to Claude

1. Go to **Claude Web → Settings → Connectors → Add custom connector**
2. Enter your server URL: `https://your-domain.com/mcp`
3. Leave client ID/secret empty — Claude auto-registers via Dynamic Client Registration
4. Claude opens your browser to the approval page
5. Enter your `MCP_AUTH_PASSWORD` and click **Approve**
6. Done — Claude can now use the email tools

Re-authorization is needed after server restart or when tokens expire (24h access / 7d refresh with auto-rotation).

## Usage

Use the built-in `review_inbox` prompt or just ask Claude to check your email. The tool instructions guide Claude to present emails one at a time and ask before drafting replies. Drafts are saved to your IMAP Drafts folder — review and send manually from your email client.

## Auth

OAuth 2.1 with PKCE. The server acts as its own authorization server with:

- Dynamic Client Registration (RFC 7591)
- Authorization Code + PKCE flow
- Password-gated approval page (only you can authorize)
- In-memory token storage (stateless — restart requires re-auth)
