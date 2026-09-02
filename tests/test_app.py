from contextlib import asynccontextmanager

import anyio
import pytest
from starlette.applications import Starlette

from emailmcp.app import _close_registry_on_shutdown


async def run_lifespan(app):
    async with app.router.lifespan_context(app):
        pass


class FakeRegistry:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


def test_the_registry_is_closed_when_the_process_stops():
    """FastMCP's own lifespan hook runs per MCP session, which is the wrong
    scope for a connection pool every session shares."""
    order = []

    @asynccontextmanager
    async def inner(_app):
        order.append("session manager up")
        yield
        order.append("session manager down")

    app = Starlette(lifespan=inner)
    registry = FakeRegistry()
    _close_registry_on_shutdown(app, registry)

    anyio.run(run_lifespan, app)

    assert registry.closed == 1
    assert order == ["session manager up", "session manager down"]


    # And still closed when the shutdown underneath it blows up.
    @asynccontextmanager
    async def failing(_app):
        yield
        raise RuntimeError("shutdown blew up")

    app = Starlette(lifespan=failing)
    registry = FakeRegistry()
    _close_registry_on_shutdown(app, registry)

    with pytest.raises(RuntimeError):
        anyio.run(run_lifespan, app)

    assert registry.closed == 1
