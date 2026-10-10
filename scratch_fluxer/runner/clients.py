"""The runner as the website sees it: start, stop and status, whether the runner is built in, remote, or not there.

The website only ever uses these three calls (plus ``close``). Which kind of runner is behind them depends on the
settings: ``LocalRunner`` runs bots inside this process's machine (development, or a site hosted on one server),
``RemoteRunner`` signs its calls and sends them to a runner service, and ``OffRunner`` says that running is not set up.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import httpx

from .manager import RunnerManager, view
from .protocol import (
    ERRORS,
    Busy,
    Refused,
    RunnerError,
    StartRequest,
    TooMany,
    Unavailable,
    sign_headers,
)

log = logging.getLogger("scratch_fluxer.runner")

OFF_MESSAGE = "Running bots isn't switched on for this site. You can still download your code and run it yourself."
DOWN_MESSAGE = "The bot runner can't be reached right now. Try again in a moment."


class RunnerClient(Protocol):
    mode: str

    async def start(self, request: StartRequest, *, replace: bool = True) -> dict[str, Any]: ...

    async def stop(self, bot_id: str, why: str = "you") -> dict[str, Any]: ...

    async def status(self, bot_id: str, *, after: int = 0, known: str | None = None) -> dict[str, Any]: ...

    async def close(self) -> None: ...


class LocalRunner:
    """Bots run as processes of this machine."""

    mode = "local"

    def __init__(self, manager: RunnerManager) -> None:
        self.manager = manager

    async def start(self, request: StartRequest, *, replace: bool = True) -> dict[str, Any]:
        return await self.manager.start(request, replace=replace)

    async def stop(self, bot_id: str, why: str = "you") -> dict[str, Any]:
        return await self.manager.stop(bot_id, why)

    async def status(self, bot_id: str, *, after: int = 0, known: str | None = None) -> dict[str, Any]:
        return self.manager.snapshot(bot_id, after, known)

    async def close(self) -> None:
        await self.manager.shutdown()


class OffRunner:
    """Running bots isn't set up. Nothing is running, and nothing can be started."""

    mode = "off"

    async def start(self, request: StartRequest, *, replace: bool = True) -> dict[str, Any]:
        raise Unavailable(OFF_MESSAGE)

    async def stop(self, bot_id: str, why: str = "you") -> dict[str, Any]:
        return view(None, 0, None)

    async def status(self, bot_id: str, *, after: int = 0, known: str | None = None) -> dict[str, Any]:
        return view(None, 0, None)

    async def close(self) -> None:
        return None


class RemoteRunner:
    """Bots run on a runner service somewhere else. Every call is signed, and nothing is followed to another address."""

    mode = "remote"

    def __init__(self, url: str, secret: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._secret = secret
        self._client = httpx.AsyncClient(
            base_url=url,
            transport=transport,
            timeout=httpx.Timeout(30.0, connect=5.0),
            follow_redirects=False,
        )

    async def start(self, request: StartRequest, *, replace: bool = True) -> dict[str, Any]:
        return await self._call(
            "PUT", f"/v1/runs/{request.bot_id}", body=request.to_json(), params={"replace": "1" if replace else "0"}
        )

    async def stop(self, bot_id: str, why: str = "you") -> dict[str, Any]:
        return await self._call("DELETE", f"/v1/runs/{bot_id}", params={"why": why})

    async def status(self, bot_id: str, *, after: int = 0, known: str | None = None) -> dict[str, Any]:
        params = {"after": str(max(0, after))}
        if known:
            params["run"] = known
        return await self._call("GET", f"/v1/runs/{bot_id}", params=params)

    async def close(self) -> None:
        await self._client.aclose()

    async def _call(
        self, method: str, path: str, *, body: bytes = b"", params: dict[str, str] | None = None
    ) -> dict[str, Any]:
        request = self._client.build_request(method, path, params=params, content=body or None)
        target = request.url.raw_path.decode("ascii")  # the path and query exactly as they are sent
        request.headers.update(sign_headers(self._secret, method, target, body))
        if body:
            request.headers["Content-Type"] = "application/json"
        try:
            response = await self._client.send(request)
        except httpx.HTTPError as exc:
            # Say what kind of failure it was, never the request: it holds a bot token.
            log.warning("The runner could not be reached: %s", type(exc).__name__)
            raise Unavailable(DOWN_MESSAGE) from None
        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError:
                data = None
            if isinstance(data, dict) and "state" in data:
                return data
            log.error("The runner answered with something that isn't a run.")
            raise Unavailable(DOWN_MESSAGE)
        raise self._error(response)

    @staticmethod
    def _error(response: httpx.Response) -> RunnerError:
        try:
            data = response.json()
        except ValueError:
            data = None
        detail = str(data.get("detail", "")) if isinstance(data, dict) else ""
        kind = str(data.get("kind", "")) if isinstance(data, dict) else ""
        cls = ERRORS.get(kind)
        if cls in (Refused, TooMany, Busy) and detail:
            return cls(detail[:300])
        if response.status_code in (401, 403) or kind == "not_allowed":
            log.error("The runner refused this site's signature (%s). RUNNER_SECRET must be the same on both.", detail)
        else:
            log.error("The runner answered %s: %s", response.status_code, detail[:200])
        return Unavailable(DOWN_MESSAGE)
