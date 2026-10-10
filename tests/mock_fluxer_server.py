"""A small Fluxer for tests: the web calls and the gateway that fluxer.py uses, on a real local port.

A real bot process (started by the runner, running the real fluxer.py) can connect to it, log in, receive messages and
reply. It runs on its own thread with its own event loop, so it works next to the synchronous Playwright tests as well
as next to asyncio tests.

    mock = MockFluxer()
    api_url = mock.start()            # "http://127.0.0.1:PORT/v1": hand this to FLUXER_API_URL
    mock.say("!ping")                 # a person writes "!ping" in a channel; every connected bot gets it
    mock.replies()                    # what bots posted in reply: ["Pong!"]
    mock.stop()
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
from typing import Any

from aiohttp import WSMsgType, web
from fluxer_mock import TOKEN, application

CHANNEL = "900000000000000001"
PERSON = {"id": "777000000000000001", "username": "alice", "discriminator": "0", "bot": False}


class MockFluxer:
    def __init__(self, applications: dict[str, dict[str, Any]] | None = None) -> None:
        self.applications = applications if applications is not None else {TOKEN: application()}
        self.requests: list[dict[str, Any]] = []  # every call to the web API: method, path, json, token
        self.identified: list[str] = []  # the tokens that logged in to the gateway
        self.presences: list[dict[str, Any]] = []
        self.close_with: int | None = None  # close the gateway with this code once a bot has logged in
        self.hold_ready = False  # log the bot in but never say READY
        self.port = 0
        self._sockets: list[web.WebSocketResponse] = []
        self._ids = itertools.count(5000)
        self._sequence = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._stop: asyncio.Event | None = None

    # ------------------------------------------------------------------------------- running it
    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def start(self) -> str:
        self._thread = threading.Thread(target=self._run, daemon=True, name="mock-fluxer")
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("the mock Fluxer did not start")
        return self.api_url

    def stop(self) -> None:
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)
        if self._thread is not None:
            self._thread.join(10)

    def _run(self) -> None:
        asyncio.run(self._serve())

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        app = web.Application(client_max_size=20 * 1024 * 1024)
        app.router.add_get("/v1/applications/@me", self._me)
        app.router.add_get("/v1/gateway/bot", self._gateway_info)
        app.router.add_get("/gateway", self._gateway)
        app.router.add_post("/v1/channels/{channel}/messages", self._post_message)
        app.router.add_route("*", "/v1/{tail:.*}", self._anything_else)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]  # noqa: SLF001
        self._ready.set()
        await self._stop.wait()
        for socket in list(self._sockets):
            await socket.close()
        await runner.cleanup()

    # ------------------------------------------------------------------------------- what tests do
    def say(self, content: str, *, channel: str = CHANNEL, author: dict[str, Any] | None = None) -> None:
        """Somebody writes a message. Every bot that is logged in receives it."""
        message = {
            "id": str(next(self._ids)),
            "channel_id": channel,
            "content": content,
            "author": author or PERSON,
            "timestamp": "2026-01-01T00:00:00+00:00",
        }
        self.push("MESSAGE_CREATE", message)

    def push(self, event: str, data: dict[str, Any]) -> None:
        assert self._loop is not None
        asyncio.run_coroutine_threadsafe(self._push(event, data), self._loop).result(10)

    def drop_gateways(self, code: int = 1000) -> None:
        """Close every gateway connection (a network failure, or Fluxer asking bots to leave)."""
        assert self._loop is not None

        async def close_all() -> None:
            for socket in list(self._sockets):
                await socket.close(code=code)

        asyncio.run_coroutine_threadsafe(close_all(), self._loop).result(10)

    def replies(self) -> list[str]:
        return [
            call["json"].get("content", "")
            for call in self.requests
            if call["method"] == "POST" and call["path"].endswith("/messages") and isinstance(call["json"], dict)
        ]

    def uploads(self) -> list[tuple[str, bytes]]:
        """Every file that bots attached to a message: (file name, content)."""
        return [item for call in self.requests for item in call.get("files", [])]

    def wait_for(self, condition, seconds: float = 15.0) -> bool:
        """True once ``condition()`` is true (checked about 20 times a second)."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if condition():
                return True
            time.sleep(0.05)
        return bool(condition())

    @property
    def connected(self) -> int:
        return len([socket for socket in self._sockets if not socket.closed])

    # ------------------------------------------------------------------------------- the web API
    @staticmethod
    def _token_of(request: web.Request) -> str:
        header = request.headers.get("Authorization", "")
        return header[4:] if header.startswith("Bot ") else ""

    def _app_for(self, request: web.Request) -> dict[str, Any] | None:
        return self.applications.get(self._token_of(request))

    @staticmethod
    def _refuse() -> web.Response:
        return web.json_response({"code": "INVALID_TOKEN", "message": "Invalid token"}, status=401)

    async def _me(self, request: web.Request) -> web.Response:
        found = self._app_for(request)
        return web.json_response(found) if found else self._refuse()

    async def _gateway_info(self, request: web.Request) -> web.Response:
        if not self._app_for(request):
            return self._refuse()
        return web.json_response({"url": f"ws://127.0.0.1:{self.port}/gateway", "shards": 1})

    async def _post_message(self, request: web.Request) -> web.Response:
        found = self._app_for(request)
        if not found:
            return self._refuse()
        body: Any
        files: list[tuple[str, bytes]] = []
        if request.content_type == "application/json":
            body = await request.json()
        else:  # a message with files: what it says, and the files that came with it
            form = await request.post()
            body = {"multipart": sorted(form.keys())}
            payload = form.get("payload_json")
            if isinstance(payload, str):
                import json

                body = {**json.loads(payload), "multipart": sorted(form.keys())}
            files = [(item.filename, item.file.read()) for item in form.values() if isinstance(item, web.FileField)]
        self.requests.append(
            {"method": "POST", "path": request.path, "json": body, "token": self._token_of(request), "files": files}
        )
        channel = request.match_info["channel"]
        return web.json_response(
            {
                "id": str(next(self._ids)),
                "channel_id": channel,
                "content": body.get("content", "") if isinstance(body, dict) else "",
                "author": {"id": found["bot"]["id"], "username": found["bot"]["username"], "bot": True},
                "timestamp": "2026-01-01T00:00:01+00:00",
            }
        )

    async def _anything_else(self, request: web.Request) -> web.Response:
        self.requests.append(
            {"method": request.method, "path": request.path, "json": None, "token": self._token_of(request)}
        )
        return web.json_response({"code": "UNKNOWN", "message": "Not part of the mock"}, status=404)

    # ------------------------------------------------------------------------------- the gateway
    async def _send(self, socket: web.WebSocketResponse, op: int, event: str | None = None, data: Any = None) -> None:
        payload: dict[str, Any] = {"op": op, "d": data}
        if event is not None:
            payload["t"] = event
            payload["s"] = next(self._sequence)
        await socket.send_json(payload)

    async def _push(self, event: str, data: dict[str, Any]) -> None:
        for socket in list(self._sockets):
            if not socket.closed:
                await self._send(socket, 0, event, data)

    async def _gateway(self, request: web.Request) -> web.WebSocketResponse:
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        self._sockets.append(socket)
        await self._send(socket, 10, data={"heartbeat_interval": 30000})
        async for message in socket:
            if message.type != WSMsgType.TEXT:
                continue
            frame = message.json()
            op = frame.get("op")
            if op == 1:
                await self._send(socket, 11)
            elif op == 2:
                token = frame["d"]["token"]
                self.identified.append(token)
                found = self.applications.get(token)
                if found is None:
                    await socket.close(code=4004)
                elif self.close_with is not None:
                    await socket.close(code=self.close_with)
                elif not self.hold_ready:
                    user = {**found["bot"], "bot": True}
                    await self._send(
                        socket,
                        0,
                        "READY",
                        {
                            "v": 1,
                            "session_id": "mock-session",
                            "resume_gateway_url": f"ws://127.0.0.1:{self.port}/gateway",
                            "user": user,
                            "guilds": [],
                        },
                    )
            elif op == 3:
                self.presences.append(frame.get("d"))
        if socket in self._sockets:
            self._sockets.remove(socket)
        return socket
