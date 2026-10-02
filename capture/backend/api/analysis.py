"""参数搜索、来源追踪与请求对比接口。"""

from fastapi import APIRouter, HTTPException, Query, Request

from capture.backend.analysis.models import SearchOptions
from capture.backend.analysis.parameters import extract_parameters
from capture.backend.analysis.service import AnalysisService
from capture.backend.context import workbench

router = APIRouter()


@router.post("/api/analysis/{session_id}/search")
def search_analysis(session_id: str, options: SearchOptions, request: Request):
    """先筛选摘要，再有界检查参数，next_offset 表示实际扫描到的位置。"""
    store = workbench(request).store
    if not options.parameter:
        result = store.list_flows(session_id, filters=options.filters)
        return {
            **result,
            "next_offset": options.filters.offset + len(result["items"]),
            "has_more": options.filters.offset + len(result["items"]) < result["total"],
            "scanned": len(result["items"]),
        }
    query = options.filters.model_copy(update={"limit": options.scan_limit})
    result = store.list_flows(session_id, filters=query)
    items, scanned, incomplete, read_ids = [], 0, 0, []
    condition = options.parameter
    selector = condition.field
    if not selector.startswith(("request.", "response.", "original_request.")):
        selector = "request." + selector
    for row in result["items"]:
        scanned += 1
        read_ids.append(row["id"])
        extracted = extract_parameters(
            store.get_flow(session_id, row["id"], preview=True)
        )
        incomplete += bool(extracted["warnings"])
        values = [
            item["value"]
            for item in extracted["fields"]
            if item["field"] == selector or item["field"].rsplit("[", 1)[0] == selector
        ]
        matched = (
            bool(values)
            if condition.operator == "exists"
            else any(
                condition.value == value
                if condition.operator == "equals"
                else condition.value in value
                for value in values
            )
        )
        if matched:
            items.append(row)
        if len(items) >= options.filters.limit:
            break
    offset = options.filters.offset + scanned
    return {
        "items": items,
        "prefilter_total": result["total"],
        "next_offset": offset,
        "has_more": offset < result["total"],
        "scanned": scanned,
        "incomplete_requests": incomplete,
        "body_preview_bytes": 65536,
        "_audit": {"read_request_ids": read_ids},
    }


@router.get("/api/analysis/{session_id}/{flow_id}/parameters")
def request_parameters(session_id: str, flow_id: str, request: Request):
    """返回字段路径，供 Agent 精确选择参数后追踪来源。"""
    return extract_parameters(
        workbench(request).store.get_flow(session_id, flow_id, preview=True)
    )


@router.get("/api/analysis/{session_id}/{flow_id}/trace")
def trace_parameter(
    session_id: str,
    flow_id: str,
    field: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    window_seconds: int = Query(300, ge=1, le=86400),
):
    """仅在指定会话、时间窗内追踪候选参数来源。"""
    try:
        return AnalysisService(workbench(request).store).trace(
            session_id, flow_id, field, limit, window_seconds
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/analysis/{session_id}/{flow_id}/chain")
def request_chain(
    session_id: str,
    flow_id: str,
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    window_seconds: int = Query(300, ge=1, le=86400),
):
    """返回已记录关系与候选值匹配，不把相邻请求推断为因果。"""
    return AnalysisService(workbench(request).store).chain(
        session_id, flow_id, limit, window_seconds
    )


@router.get("/api/analysis/{session_id}/{flow_id}/compare")
def compare_requests(
    session_id: str,
    flow_id: str,
    other_session_id: str,
    other_flow_id: str,
    request: Request,
):
    """比较两条明确指定的请求，可跨历史与重放会话。"""
    return AnalysisService(workbench(request).store).compare(
        session_id, flow_id, other_session_id, other_flow_id
    )
