import base64
import json
from datetime import datetime, timezone

import messages
import pytest
from fakes import ADDRESS, FakeConnection, FakeIMAP, meta
from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ImageContent, TextContent

from emailmcp import tools
from emailmcp.config import Mailbox
from emailmcp.registry import Registry

ENV = {"MAILBOX_ANDRSK_CZ_PASSWORD": "one"}
MAILBOXES = [Mailbox(key="andrsk.cz", address=ADDRESS)]


def build(imap: FakeIMAP | None = None) -> FastMCP:
    mcp = FastMCP("test")
    registry = Registry(MAILBOXES, ENV)
    if imap is not None:
        registry.get("andrsk.cz")._conn = FakeConnection(imap)
    tools.register(mcp, registry)
    return mcp


@pytest.fixture
def served():
    msg = messages.with_inline_images(messages.png(300, 200))
    metadata = {
        1: meta(
            "Alice", "alice@example.com", "Screenshots", "<i1@example.com>",
            datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc),
        )
    }
    return build(FakeIMAP(metadata, {1: msg.as_bytes()}, threads=((1,),)))


async def call(mcp: FastMCP, name: str, **arguments) -> CallToolResult:
    return await mcp._tool_manager.call_tool(name, arguments, convert_result=True)


def test_only_the_content_tools_opt_out_of_a_structured_result():
    """An output schema forces the result through model validation, which rules
    out returning interleaved text and images. This is the SDK contract the
    whole feature rests on, and an `mcp` bump could take it away silently."""
    registered = {tool.name: tool for tool in build()._tool_manager.list_tools()}

    assert set(registered) == {
        "list_mailboxes", "list_inbox", "read_email",
        "read_attachment", "draft_reply_email", "draft_new_email",
    }
    assert registered["read_email"].output_schema is None
    assert registered["read_attachment"].output_schema is None
    assert registered["list_inbox"].output_schema is not None
    assert registered["list_mailboxes"].output_schema is not None
    assert set(registered["read_attachment"].parameters["properties"]) == {"email_id", "part_id"}


@pytest.mark.anyio
async def test_read_email_reaches_the_client_as_interleaved_blocks(served):
    result = await call(served, "read_email", email_id="andrsk.cz:1")

    assert isinstance(result, CallToolResult)
    assert result.structuredContent is None  # no duplicate copy of the image
    assert [type(block) for block in result.content] == [
        TextContent, TextContent, ImageContent, TextContent
    ]

    metadata = json.loads(result.content[0].text)
    assert metadata["subject"] == "Screenshots"
    assert result.content[1].text == "before 0"
    assert result.content[2].mimeType == "image/jpeg"
    assert base64.b64decode(result.content[2].data)[:2] == b"\xff\xd8"  # a real JPEG
    assert result.content[3].text == "after 0"


@pytest.mark.anyio
async def test_read_attachment_reaches_the_client_as_one_block(served):
    result = await call(served, "read_attachment", email_id="andrsk.cz:1", part_id="2.2")
    assert [type(block) for block in result.content] == [ImageContent]
