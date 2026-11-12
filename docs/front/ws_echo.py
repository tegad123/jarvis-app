#!/usr/bin/env python3
"""A stand-in account for verify_front.mjs: a WebSocket server on loopback that
tells the client what arrived (path, Origin, whether a cookie came, the
forwarded headers, the subprotocol) and then echoes. Every connection it
accepts is appended to the log file, so "never reached the account" is a
line count, not a guess.

    python ws_echo.py <port, 0 for any> <log>      prints the port once listening
"""
import asyncio
import json
import sys

from websockets.asyncio.server import serve


async def main(port: int, log: str) -> None:
    async def handler(ws):
        req = ws.request
        seen = {"path": req.path, "origin": req.headers.get("origin"), "cookie": req.headers.get("cookie"),
                "xff": req.headers.get("x-forwarded-for"), "xfh": req.headers.get("x-forwarded-host"),
                "proto": ws.subprotocol}
        with open(log, "a") as f:
            f.write(json.dumps(seen) + "\n")
        await ws.send(json.dumps(seen).encode())
        async for msg in ws:
            await ws.send(msg)

    async with serve(handler, "127.0.0.1", port, subprotocols=["binary"], compression=None) as server:
        print(server.sockets[0].getsockname()[1], flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
