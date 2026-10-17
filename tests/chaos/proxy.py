"""A tiny TCP fault-injection proxy (in the spirit of Toxiproxy) for partitioning a service from its dependencies.

Processes under test connect to the proxy instead of PostgreSQL or Redis. ``cut()`` drops every open connection and
refuses new ones until ``restore()``: from the client's side this is indistinguishable from the server restarting
or the network failing, but it leaves the shared database and Redis untouched for everything else.
"""

from __future__ import annotations

import asyncio
import contextlib


class ChaosProxy:
    def __init__(self, target_host: str, target_port: int) -> None:
        self.target = (target_host, target_port)
        self.port = 0
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.StreamWriter] = set()
        self._pumps: set[asyncio.Task[None]] = set()
        self.cuts = 0
        self.down = False

    async def start(self) -> ChaosProxy:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    def url_for(self, url: str) -> str:
        """Rewrite a database/Redis URL so it points at the proxy."""
        host, port = self.target
        return url.replace(f"{host}:{port}", f"127.0.0.1:{self.port}", 1)

    async def cut(self) -> None:
        self.down = True
        self.cuts += 1
        for writer in list(self._connections):
            writer.transport.abort()  # RST, not a polite FIN: like a crash or a pulled cable
        self._connections.clear()

    def restore(self) -> None:
        self.down = False

    async def close(self) -> None:
        await self.cut()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for pump in list(self._pumps):
            pump.cancel()
        await asyncio.gather(*self._pumps, return_exceptions=True)

    async def _accept(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        if self.down:
            client_writer.transport.abort()
            return
        try:
            upstream_reader, upstream_writer = await asyncio.open_connection(*self.target)
        except OSError:
            client_writer.transport.abort()
            return
        self._connections.update({client_writer, upstream_writer})
        for reader, writer in ((client_reader, upstream_writer), (upstream_reader, client_writer)):
            task = asyncio.create_task(self._pump(reader, writer))
            self._pumps.add(task)
            task.add_done_callback(self._pumps.discard)

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass  # the other side went away (often because we cut it); nothing to forward any more
        finally:
            self._connections.discard(writer)
            with contextlib.suppress(Exception):
                writer.transport.abort()
