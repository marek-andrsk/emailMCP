"""MCP prompts."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .registry import Registry


def register(mcp: FastMCP, registry: Registry) -> None:
    keys = ", ".join(registry.keys)

    @mcp.prompt(
        description=(
            f"Review inbox emails one by one, with option to draft replies. "
            f"Optionally pass a mailbox key ({keys}); otherwise all mailboxes are reviewed in turn."
        )
    )
    def review_inbox(mailbox: str = "") -> str:
        if mailbox.strip():
            scope = f"Check the {mailbox.strip()} mailbox"
        else:
            scope = (
                f"Check my mailboxes one at a time ({keys}), finishing one before starting the next. "
                f"Tell me which mailbox we are in"
            )
        return (
            f"{scope} and walk me through the emails one by one. "
            "For each email: summarize it (keep long emails concise), then ask if I want to reply or skip. "
            "If I want to reply, I'll give you context about what to say and you'll draft the reply "
            "and save it to the Drafts folder of that same mailbox. Never send emails directly."
        )
