"""独立链路分析任务及保存视图接口。"""

from fastapi import APIRouter, HTTPException, Query, Request

from capture.backend.context import workbench
from capture.backend.links.models import LinkOptions, LinkView

router = APIRouter()


@router.post("/api/links/jobs")
async def start_link_analysis(options: LinkOptions, request: Request):
    """创建独立分析任务；不会在代理或 Web 进程解析报文。"""
    try:
        return await workbench(request).analysis.start(options)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/links/jobs/{job_id}")
def link_analysis_status(job_id: str, request: Request):
    try:
        return workbench(request).analysis.status(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/links/jobs/{job_id}/result")
def link_analysis_result(
    job_id: str,
    request: Request,
    offset: int = Query(0, ge=0, le=2000),
    limit: int = Query(100, ge=1, le=100),
):
    """图与字段结果分页读取，避免把整个会话一次传给页面或 Agent。"""
    try:
        return workbench(request).analysis.result(job_id, offset, limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/links/jobs/{job_id}/cancel")
async def cancel_link_analysis(job_id: str, request: Request):
    try:
        return await workbench(request).analysis.cancel(job_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/links/views")
def saved_link_views(request: Request):
    return workbench(request).analysis.views()


@router.post("/api/links/views")
def save_link_view(view: LinkView, request: Request):
    return workbench(request).analysis.save_view(view)


@router.put("/api/links/views/{view_id}")
def update_link_view(view_id: str, view: LinkView, request: Request):
    try:
        return workbench(request).analysis.save_view(view, view_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/links/views/{view_id}")
def saved_link_view(view_id: str, request: Request):
    try:
        return workbench(request).analysis.view(view_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
