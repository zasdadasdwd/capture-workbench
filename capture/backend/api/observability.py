"""MCP 查询审计日志与实时 SSE 接口。"""

import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from capture.backend.context import workbench

router = APIRouter()


@router.get("/api/mcp/logs")
def mcp_logs(request: Request, limit: int = Query(100, ge=1, le=100)):
    """读取最近的查询日志，不加载抓包正文。"""
    return workbench(request).query_logs.read(limit)


@router.get("/api/mcp/logs/stream")
async def mcp_log_stream(request: Request):
    """SSE 推送日志快照；每秒仅检查文件属性，变化时才读有界尾部。"""
    logs = workbench(request).query_logs

    async def events():
        revision = None
        ticks = 0
        while not await request.is_disconnected():
            current = await asyncio.to_thread(logs.revision)
            if current != revision:
                snapshot = await asyncio.to_thread(logs.read)
                yield "data: " + json.dumps(snapshot, ensure_ascii=False) + "\n\n"
                revision = current
            elif ticks % 15 == 0:
                yield ": heartbeat\n\n"
            ticks += 1
            await asyncio.sleep(1)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
