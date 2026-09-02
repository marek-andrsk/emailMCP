"""MCP tool surface.

The tools stay thin: resolve a mailbox (or a message id, which carries its own
mailbox) and delegate to that mailbox's store. Descriptions are generated from
the loaded configuration so the valid mailbox keys are visible to the model
without an extra round trip.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult

from . import blocks
from .registry import Registry


def register(mcp: FastMCP, registry: Registry) -> None:
    keys = ", ".join(registry.keys)
    example_id = f"{registry.keys[0]}:4211" if registry.keys else "mailbox:4211"
    id_note = (
        f"Message ids are '<mailbox>:<uid>' (for example '{example_id}') — always pass the "
        f"'id' value exactly as returned; never combine a uid from one mailbox with another "
        f"mailbox's key. Configured mailboxes: {keys}."
    )

    @mcp.tool(
        description=(
            "List the configured mailboxes with their keys and addresses. "
            "Use this when you need to know which mailboxes exist or which address a key belongs to."
        )
    )
    def list_mailboxes() -> list[dict]:
        return registry.describe()

    @mcp.tool(
        description=(
            f"List inbox threads for ONE mailbox (latest message per thread). Requires the mailbox "
            f"key — one of: {keys}. Returns metadata only: id, mailbox, from, subject, date, snippet, "
            f"thread_ids, needs_reply. "
            "After calling this, present threads to the user ONE AT A TIME using read_email on the id. "
            "For each email: summarize it, then ask the user if they want to reply, skip, or stop reviewing. "
            "Do NOT read all emails at once. "
            "To review more than one mailbox, call this once per mailbox and finish one before starting the next."
        )
    )
    def list_inbox(mailbox: str) -> list[dict]:
        return registry.get(mailbox).list_inbox()

    @mcp.tool(
        description=(
            "Read the full content of a single email. Thread-aware. "
            "Returns a JSON block with from, to, cc, subject, date, message_id, references, "
            "attachments, thread_ids, thread_context and needs_reply, followed by the body: "
            "text interleaved with the images that appear in it, in the order they appear. "
            "Images too large or too numerous to inline are named in the text instead, with the "
            "part_id to pass to read_attachment. "
            "Use this to show the user a summary of the email content. " + id_note
        )
    )
    def read_email(email_id: str) -> CallToolResult:
        store, uid = registry.resolve(email_id)
        return blocks.email_result(store.read_email(uid))

    @mcp.tool(
        description=(
            "Read one attachment or inline image of an email, by the part_id listed in "
            "read_email's 'attachments' (or named in its body). Images come back as images, "
            "text attachments as text; other formats cannot be read. "
            "Call this only for a part you actually need — attachments can be large. " + id_note
        )
    )
    def read_attachment(email_id: str, part_id: str) -> CallToolResult:
        store, uid = registry.resolve(email_id)
        return blocks.segment_result(store.read_attachment(uid, part_id))

    @mcp.tool(
        description=(
            "Save a reply draft to the Drafts folder of the mailbox the original email lives in. "
            "This does NOT send the email — the user reviews and sends it manually from their email client. "
            "Provide the id of the email being replied to and the plain text reply body. "
            "The draft gets correct In-Reply-To and References headers for proper threading, and is sent "
            "from the address of that mailbox. " + id_note
        )
    )
    def draft_reply_email(reply_to_id: str, body: str) -> str:
        store, uid = registry.resolve(reply_to_id)
        return store.save_reply_draft(uid, body)

    @mcp.tool(
        description=(
            f"Save a NEW (non-reply) draft to a mailbox's Drafts folder. This does NOT send the email. "
            f"Requires the mailbox key — one of: {keys} — because it determines the sender address. "
            f"If it is not obvious which mailbox to send from, ask the user. "
            f"No reply headers or quoted thread context are added."
        )
    )
    def draft_new_email(mailbox: str, to: str, subject: str, body: str) -> str:
        return registry.get(mailbox).save_new_draft(to, subject, body)
