"""WebSocket 轻量失效通知通道。"""

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from capture.backend.context import local_origin

router = APIRouter()


@router.websocket("/ws")
async def live_updates(socket: WebSocket):
    """推送数据变更与心跳；断线后前端重新查询数据库补齐列表。"""
    host = socket.headers.get("host", "")
    if host.split(":")[0] not in (
        "localhost",
        "127.0.0.1",
        "testserver",
    ) or not local_origin(socket.headers.get("origin"), host):
        await socket.close(code=1008)
        return
    await socket.accept()
    state = socket.app.state.workbench
    queue = asyncio.Queue(maxsize=32)
    state.subscribers.add(queue)
    try:
        await socket.send_json({"type": "connected"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), 15)
            except asyncio.TimeoutError:
                event = {"type": "heartbeat"}
            await socket.send_json(event)
    except (WebSocketDisconnect, RuntimeError, OSError):
        pass
    finally:
        state.subscribers.discard(queue)
