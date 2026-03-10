# Email MCP Server — Implementation Plan

## Context
Build a personal hosted MCP server that gives Claude read access to an IMAP inbox and the ability to save reply drafts. No send capability. Deployed via Nixpacks to Coolify.

## Project: `~/Sites/emailMCP/`

## Files to Create

```
~/Sites/emailMCP/
├── server.py           # FastMCP app, tool definitions, auth middleware
├── imap_client.py      # IMAP connection pool + operations (list, read, save draft)
├── .env.example        # Template for required env vars
├── requirements.txt    # mcp[cli], imapclient, html2text, python-dotenv
└── Procfile            # Nixpacks entry point: web: python server.py
```

No Dockerfile needed — Nixpacks detects Python from `requirements.txt`.

## Dependencies

- `mcp[cli]` — FastMCP server with streamable HTTP transport
- `imapclient` — clean IMAP interface
- `html2text` — convert HTML email bodies to markdown for Claude
- `python-dotenv` — load `.env`
- `uvicorn` — ASGI server (used by FastMCP internally)

## Environment Variables (`.env`)

```
IMAP_HOST=mail.example.com
IMAP_PORT=993
IMAP_USER=user@example.com
IMAP_PASS=secret
MCP_AUTH_TOKEN=random-long-secret
PORT=8000
```

## Architecture

### Auth (server.py)
- Bearer token auth via FastMCP's `streamable_http_app()` + Starlette middleware
- Checks `Authorization: Bearer <MCP_AUTH_TOKEN>` on every request
- Returns 401 if missing/wrong

### IMAP Client (imap_client.py)
- Single persistent IMAP connection, auto-reconnect on timeout/disconnect
- Context manager pattern for safe connection handling
- All operations are **read-only** except `save_draft` which uses IMAP `APPEND`

### MCP Tools (3 tools in server.py)

**1. `list_inbox()`**
- Fetches all messages in INBOX (not Archive)
- Returns list of: `uid`, `from`, `subject`, `date`, `snippet` (first ~150 chars of body)
- Tool description instructs Claude to present emails ONE AT A TIME using `read_email`

**2. `read_email(uid: str)`**
- Fetches full email by UID
- Parses MIME: prefers plain text, falls back to HTML→markdown via `html2text`
- Returns: `from`, `to`, `cc`, `subject`, `date`, `body`, `message_id`, `references`
- Strips quoted reply chains to keep context clean

**3. `save_draft(reply_to_uid: str, body: str)`**
- Reads original email to get `Message-ID`, `Subject`, `From` for headers
- Constructs a proper reply: `In-Reply-To`, `References`, `Re: Subject`
- `To:` set to original sender, `From:` set to IMAP_USER
- Saves to Drafts folder via `IMAP APPEND` with `\Draft` flag
- Returns confirmation with subject line

### MCP Prompt (server.py)

**`review_inbox`** — one-click prompt template:
> "Check my inbox and walk me through emails one by one. For each: summarize it, ask if I want to reply or skip. If I want to reply, I'll give you context and you draft the reply and save it to my Drafts folder."

## Verification

1. Run locally: `python server.py` — should start on port 8000
2. Test with `mcp dev server.py` (MCP Inspector) or curl the health endpoint
3. Add to Claude Desktop config pointing to `http://localhost:8000/mcp/`
4. Test flow: list inbox → read email → save draft → verify draft appears in email client
