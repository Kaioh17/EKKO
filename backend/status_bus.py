"""In-memory WS pub/sub for pushed status. Nothing else in the repo does
pub/sub, so this is new -- everything else in backend/ is a thin wrap
around already-wired code.

Usage:
    python backend/status_bus.py          # run the self-check
"""

import asyncio

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            targets = list(self._connections)
        dead = []
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._connections:
                        self._connections.remove(ws)


status_bus = ConnectionManager()


if __name__ == "__main__":
    class _FakeSocket:
        def __init__(self, fail: bool = False) -> None:
            self.fail = fail
            self.sent: list[dict] = []

        async def accept(self) -> None:
            pass

        async def send_json(self, message: dict) -> None:
            if self.fail:
                raise RuntimeError("closed")
            self.sent.append(message)

    async def _demo() -> None:
        mgr = ConnectionManager()
        alive, dead = _FakeSocket(), _FakeSocket(fail=True)
        await mgr.connect(alive)
        await mgr.connect(dead)
        await mgr.broadcast({"type": "status", "state": "listening"})
        assert alive.sent == [{"type": "status", "state": "listening"}]
        assert dead not in mgr._connections
        assert alive in mgr._connections
        print("status_bus self-check passed")

    asyncio.run(_demo())
