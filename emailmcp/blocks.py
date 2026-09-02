"""Segments to MCP content blocks.

The MCP wire types stop here. Everything below this module speaks in segments,
which is what lets the body renderer stay a pure function of a MIME message.
"""

from __future__ import annotations

import base64
import json

from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent

from .imap.content import ImageSegment, Segment
from .imap.store import EmailContent


def email_result(email: EmailContent) -> CallToolResult:
    """Metadata first, then the body interleaved with the images it contains."""
    content: list[ContentBlock] = [
        TextContent(type="text", text=json.dumps(email.metadata, ensure_ascii=False, indent=2))
    ]
    content.extend(_block(segment) for segment in email.segments)
    return CallToolResult(content=content)


def segment_result(segment: Segment) -> CallToolResult:
    return CallToolResult(content=[_block(segment)])


def _block(segment: Segment) -> ContentBlock:
    if isinstance(segment, ImageSegment):
        return ImageContent(
            type="image",
            data=base64.b64encode(segment.image.data).decode(),
            mimeType=segment.image.mime_type,
        )
    return TextContent(type="text", text=segment.text)
