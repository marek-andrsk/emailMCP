# Email MCP Server

Personal MCP server that gives Claude read access to one or more IMAP mailboxes and the ability to save reply drafts. No send capability by design.

## Tools

| Tool | Description |
|---|---|
| `list_mailboxes` | List the configured mailboxes with their keys and addresses |
| `list_inbox` | List inbox threads for one mailbox (latest message per thread; includes needs_reply + thread_ids) |
| `read_email` | Read full email content by id (thread-aware; includes thread_context + needs_reply) |
| `draft_reply_email` | Save a reply draft to the Drafts folder of the mailbox the original lives in (does NOT send) |
| `draft_new_email` | Save a NEW (non-reply) draft to a mailbox's Drafts folder (does NOT send) |

### Message ids

IMAP UIDs are only unique within one mailbox, so every id the server returns carries its mailbox: `andrsk.cz:4211`. Pass those ids back verbatim — the mailbox travels with the UID, so a UID from one mailbox can never be paired with another mailbox's key.

`list_inbox` and `draft_new_email` take a mailbox key instead, since no message id is available yet. `list_inbox` deliberately covers one mailbox at a time; to review several, call it once per mailbox.

## Setup

### 1. Mailboxes

Mailboxes are defined by `MAILBOXES_JSON` — a JSON array of non-secret mailbox objects. That is the only source: one variable, no config file, no precedence rules. It reads from `.env` locally and from the platform's environment in production.

```
MAILBOXES_JSON='[
  {"key": "example.com", "label": "Example", "address": "me@example.com", "host": "imap.migadu.com"},
  {"key": "second.example", "address": "me@second.example", "host": "imap.migadu.com"}
]'
```

A quoted multi-line value works in both `.env` and a Coolify-style textarea, so the list stays readable as it grows.

| Field | Required | Default | Notes |
|---|---|---|---|
| `key` | yes | — | Lowercase `a-z0-9._-`, appears in every message id. A domain reads well. |
| `address` | yes | — | IMAP username and the `From` of drafts |
| `label` | no | `address` | Shown in confirmations |
| `host` | no | `imap.migadu.com` | |
| `port` | no | `993` | TLS only |
| `inbox_folder` | no | `INBOX` | |
| `drafts_folder` | no | `Drafts` | `null` discovers it via the IMAP special-use flag |
| `sent_folder` | no | `null` | `null` discovers it; used to detect already-answered threads |

### 2. Environment variables

```
MAILBOXES_JSON=[{"key":"example.com","address":"me@example.com","host":"imap.migadu.com"}]
MAILBOX_EXAMPLE_COM_PASSWORD=imap-password-for-that-mailbox

MCP_AUTH_PASSWORD=pick-a-strong-password-for-oauth-approval
BASE_URL=https://your-mcp-server.example.com
MCP_ALLOWED_HOSTS=your-mcp-server.example.com
PORT=8000
```

Each mailbox needs its password in `MAILBOX_<KEY>_PASSWORD`, where `<KEY>` is the key uppercased with every non-alphanumeric character replaced by `_` — so `andrsk.cz` reads `MAILBOX_ANDRSK_CZ_PASSWORD`.

The configuration is validated at startup and reports every problem at once — a bad key, an unknown field and a missing password all appear in one message rather than one per restart.

### 3. Adding a mailbox

Add an object to the JSON, set its `MAILBOX_<KEY>_PASSWORD`, redeploy. No code changes.

### 4. Run locally

```bash
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt && python server.py
```

Server starts at `http://localhost:8000`. Health check at `/health` (it also lists the loaded mailbox keys).

### 5. Deploy

The `Procfile` works out of the box with Railway, Render, Coolify, Heroku, or any Nixpacks/Buildpack host. Push and set the env vars. `BASE_URL` must match your public domain; HTTPS is required for OAuth.

## Connect to Claude

1. **Claude → Settings → Connectors → Add custom connector**
2. Server URL: `https://your-domain.com/mcp`
3. Leave client ID/secret empty — Claude auto-registers via Dynamic Client Registration
4. Enter your `MCP_AUTH_PASSWORD` on the approval page

Re-authorization is needed after a server restart or when tokens expire (24h access / 7d refresh with auto-rotation).

## Usage

Use the `review_inbox` prompt (optionally with a mailbox key) or just ask Claude to check your email. The tool descriptions carry the valid mailbox keys, so Claude knows what exists without an extra call. Drafts land in the Drafts folder of the mailbox the conversation belongs to — review and send manually from your email client.

## Layout

```
emailmcp/
├── config.py       mailbox model + loader (fails fast, reports everything)
├── refs.py         "<mailbox>:<uid>" message references
├── registry.py     key → store lookup
├── imap/
│   ├── mime.py         pure MIME/header parsing
│   ├── threads.py      IMAP THREAD parsing
│   ├── quoting.py      quoted thread blocks
│   ├── folders.py      special-use folder discovery
│   ├── connection.py   connection lifecycle, locking, folder selection
│   └── store.py        the four operations, one instance per mailbox
├── tools.py        MCP tools (descriptions generated from config)
├── prompts.py      review_inbox
├── auth/           OAuth provider + approval page
└── app.py          wiring
```

Everything below `store` is free of IMAP state and tested directly:

```bash
pip install -r requirements-dev.txt && pytest
```

## Auth

OAuth 2.1 with PKCE. The server is its own authorization server:

- Dynamic Client Registration (RFC 7591)
- Authorization Code + PKCE
- Password-gated approval page (only you can authorize)
- In-memory token storage (stateless — restart requires re-auth)

One approval covers all configured mailboxes.
