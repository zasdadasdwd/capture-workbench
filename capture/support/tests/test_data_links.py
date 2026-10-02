"""数据链路分析：只读隔离、独立进程、时序、转换、增量与人工视图。"""

import asyncio
import base64
import json
import multiprocessing
from types import SimpleNamespace

import httpx
import pytest

import capture.backend.app as app_module
from capture.backend.links.manager import AnalysisManager
from capture.backend.links.models import LinkOptions, LinkView
from capture.backend.links.worker import analyze
from capture.backend.storage import Store
from config import Settings


@pytest.fixture
def capture(tmp_path):
    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())

    def save(id, started, token="secret-token-123", response=False, duration=10):
        flow = {
            "id": id,
            "host": "example.test",
            "url": f"https://example.test/{id}",
            "method": "POST",
            "status": "complete",
            "code": 200,
            "started": started,
            "duration": duration,
            "request": {
                "url": f"https://example.test/{id}",
                "headers": [["Authorization", "Bearer " + token]],
                "body_b64": base64.b64encode(
                    json.dumps({"token": token}).encode()
                ).decode(),
            },
        }
        if response:
            flow["response"] = {
                "headers": [
                    ["Set-Cookie", "sid=" + token + "; Path=/"],
                    ["Set-Cookie", "sid=second-token; Path=/"],
                ],
                "body_b64": base64.b64encode(
                    json.dumps({"token": token}).encode()
                ).decode(),
            }
        store.save_flow(session, flow)

    save("origin", 100, response=True, duration=1000)
    save("overlap", 100.5)
    save("next", 102)
    save("unrelated", 103, token="other-token-123")
    save("contains", 104, token="prefix-secret-token-123-suffix")
    yield store, session, save, tmp_path
    store.close()


def run(capture, **kwargs):
    store, session, _, tmp_path = capture
    options = LinkOptions(
        session_id=session, flow_id="origin", field="response.body#/token", **kwargs
    )
    return analyze(
        store.root,
        tmp_path / "analysis",
        options,
        multiprocessing.Event(),
        lambda progress: None,
    )


def test_fields_cookie_versions_mask_and_value_search(capture):
    result = run(capture, operation="fields", query="sid", query_scope="field")
    assert [item["field"] for item in result["items"]] == [
        "response.cookies.sid[0]",
        "response.cookies.sid[1]",
    ]
    assert all("secret-token" not in item["value"] for item in result["items"])
    found = run(
        capture,
        operation="fields",
        query="second-token",
        query_scope="value",
        reveal=True,
    )
    assert any(
        item["field"] == "response.cookies.sid[1]" and item["value"] == "second-token"
        for item in found["items"]
    )


def test_forward_trace_time_transforms_and_contains(capture):
    result = run(capture)
    assert {node["id"] for node in result["nodes"]} == {"origin", "next"}
    assert any(edge["relation"] == "transform" for edge in result["edges"])
    assert all(edge["available_before_target"] for edge in result["edges"])
    assert "secret-token-123" not in json.dumps(result)
    overlap = run(capture, include_overlap=True, contains=True)
    assert {node["id"] for node in overlap["nodes"]} == {
        "origin",
        "next",
        "overlap",
        "contains",
    }
    assert any(not edge["available_before_target"] for edge in overlap["edges"])
    assert any(edge["relation"] == "contains" for edge in overlap["edges"])


def test_incremental_reuses_unchanged_and_rechecks_modified_deleted(capture):
    store, session, save, tmp_path = capture
    first = run(capture)
    save("next", 102, token="changed-token")
    save("new", 105)
    options = LinkOptions(
        session_id=session, flow_id="origin", field="response.body#/token"
    )
    second = analyze(
        store.root,
        tmp_path / "analysis",
        options,
        multiprocessing.Event(),
        lambda event: None,
        first,
    )
    assert {node["id"] for node in second["nodes"]} == {"origin", "new"}
    assert second["scope"]["reused"] >= 1
    store.delete_flows(session, ["new"])
    third = analyze(
        store.root,
        tmp_path / "analysis",
        options,
        multiprocessing.Event(),
        lambda event: None,
        second,
    )
    assert [node["id"] for node in third["nodes"]] == ["origin"]


def test_read_only_snapshot_and_common_value_guard(capture):
    store, session, _, tmp_path = capture
    with store.connect(session) as db:
        before = [tuple(row) for row in db.execute("SELECT * FROM flows ORDER BY id")]
    run(capture)
    with store.connect(session) as db:
        after = [tuple(row) for row in db.execute("SELECT * FROM flows ORDER BY id")]
    assert before == after
    # 参数字段中不存在的路径不会自动退回整包模糊搜索。
    with pytest.raises(ValueError):
        analyze(
            store.root,
            tmp_path / "analysis",
            LinkOptions(
                session_id=session, flow_id="origin", field="response.body#/missing"
            ),
            multiprocessing.Event(),
            lambda event: None,
        )


def test_process_manager_pagination_saved_view_and_cancellation(capture, monkeypatch):
    store, session, _, tmp_path = capture

    async def scenario():
        manager = AnalysisManager(store.root, tmp_path / "analysis")
        try:
            job = await manager.start(
                LinkOptions(
                    session_id=session, flow_id="origin", field="response.body#/token"
                )
            )
            child = manager.jobs[job["id"]]["process"]
            assert child.pid is not None and child.pid != __import__("os").getpid()
            with pytest.raises(RuntimeError):
                await manager.start(
                    LinkOptions(
                        session_id=session, flow_id="origin", operation="fields"
                    )
                )
            await manager.jobs[job["id"]]["task"]
            assert manager.status(job["id"])["status"] == "complete"
            result = manager.result(job["id"], 0, 1)
            assert len(result["nodes"]) == 1 and result["has_more"]
            second = manager.result(job["id"], 1, 1)
            assert second["nodes"][0]["id"] == "next" and second["edges"]
            view = manager.save_view(
                LinkView(
                    job_ids=[job["id"]],
                    annotations={
                        "next": {"judgment": "confirmed", "note": "local test"}
                    },
                )
            )
            store.delete_flows(session, ["next"])
            assert "next" in manager.view(view["id"])["missing_requests"]
            job2 = await manager.start(
                LinkOptions(session_id=session, flow_id="origin", operation="fields")
            )
            await manager.cancel(job2["id"])
            assert manager.status(job2["id"])["status"] == "cancelled"
            assert manager.active is None
            with pytest.raises(ValueError):
                manager.status("../capture.sqlite")
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_api_and_timeout_leave_capture_available(capture, monkeypatch):
    store, session, _, tmp_path = capture

    async def scenario():
        manager = AnalysisManager(store.root, tmp_path / "analysis", timeout=0.001)
        monkeypatch.setattr(
            app_module.app.state,
            "workbench",
            SimpleNamespace(analysis=manager, store=store),
            raising=False,
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app_module.app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/api/links/jobs",
                json={
                    "session_id": session,
                    "flow_id": "origin",
                    "operation": "fields",
                },
            )
            assert response.status_code == 200
            job = response.json()
            await manager.jobs[job["id"]]["task"]
            assert manager.status(job["id"])["status"] == "timeout"
            assert store.list_flows(session)["total"] == 5
            invalid = await client.post(
                "/api/links/jobs",
                json={
                    "session_id": session,
                    "flow_id": "origin",
                    "field": "response.body#/token",
                    "scan_limit": 100000,
                },
            )
            assert invalid.status_code == 422
        await manager.close()

    asyncio.run(scenario())


def test_failed_spawn_and_abandoned_task_are_terminal(capture, monkeypatch):
    """启动失败或后端重启不会遗留永远 running 的任务。"""
    from uuid import uuid4

    store, session, _, tmp_path = capture

    async def scenario():
        manager = AnalysisManager(store.root, tmp_path / "analysis")

        class FailedProcess:
            def start(self):
                raise OSError("test startup failure")

            def close(self):
                pass

        monkeypatch.setattr(
            manager.context, "Process", lambda **kwargs: FailedProcess()
        )
        with pytest.raises(RuntimeError):
            await manager.start(
                LinkOptions(session_id=session, flow_id="origin", operation="fields")
            )
        assert manager.active is None
        failed = next((manager.directory / "jobs").glob("*.json"))
        assert manager.status(failed.stem)["status"] == "error"
        abandoned = uuid4().hex
        manager.write(
            manager.path("jobs", abandoned), {"id": abandoned, "status": "running"}
        )
        assert manager.status(abandoned)["status"] == "interrupted"
        await manager.close()

    asyncio.run(scenario())


def test_incremental_detects_changed_source_body_file(capture):
    """原始正文被替换时，旧的源值匹配必须全部失效。"""
    store, session, _, tmp_path = capture
    first = run(capture)
    with store.connect(session) as db:
        detail = json.loads(
            db.execute("SELECT detail FROM flows WHERE id='origin'").fetchone()[0]
        )
    path = store.root / session / "bodies" / detail["response"]["body_file"]
    path.write_text(json.dumps({"token": "replacement-secret-999"}))
    result = analyze(
        store.root,
        tmp_path / "analysis",
        LinkOptions(session_id=session, flow_id="origin", field="response.body#/token"),
        multiprocessing.Event(),
        lambda event: None,
        first,
    )
    assert [node["id"] for node in result["nodes"]] == ["origin"]
    assert result["scope"]["reused"] == 0


def test_selected_fragment_and_target_scope(capture):
    """片段来自真实字段，目标 ID 限制在 SQL 查询前生效。"""
    result = run(capture, value_range=(7, 12), request_ids=["next"], contains=True)
    assert {node["id"] for node in result["nodes"]} == {"origin", "next"}
    assert all(edge["relation"] == "contains" for edge in result["edges"])
    assert result["source"]["fingerprint"] != run(capture)["source"]["fingerprint"]
    assert run(capture, request_ids=[])["scope"]["matched_requests"] == 0
    with pytest.raises(ValueError, match="内容已改变"):
        run(capture, value_range=(0, 1000))
    with pytest.raises(ValueError, match="范围无效"):
        run(capture, value_range=(5, 2))


def test_live_filter_includes_new_matching_requests(capture):
    """实时筛选保存规则，新增匹配请求也纳入下一轮分析。"""
    result = run(capture, target_filters={"search": "/next"})
    assert {node["id"] for node in result["nodes"]} == {"origin", "next"}
    capture[2]("next-new", 110)
    result = run(capture, target_filters={"search": "/next"})
    assert {node["id"] for node in result["nodes"]} == {"origin", "next", "next-new"}


def test_backwards_source_and_later_use_are_separate(capture):
    store, session, _, tmp_path = capture
    result = analyze(
        store.root,
        tmp_path / "analysis",
        LinkOptions(
            session_id=session,
            flow_id="next",
            field="request.body#/token",
            contains=True,
            value_range=(7, 12),
            direction="both",
        ),
        multiprocessing.Event(),
        lambda _: None,
    )
    sources = [edge for edge in result["edges"] if edge["role"] == "source_candidate"]
    assert any(
        edge["from"] == "origin"
        and edge["to"] == "next"
        and edge["source_field"] == "response.body#/token"
        for edge in sources
    )
    assert any(
        edge["source_field"] == "response.body#/token" and edge["full_value_match"]
        for edge in sources
    )
    assert any(
        edge["role"] == "earlier_use" and edge["from"] == "overlap"
        for edge in result["edges"]
    )
    assert any(
        edge["role"] == "later_use"
        and edge["to"] == "contains"
        and not edge["full_value_match"]
        for edge in result["edges"]
    )
    forward = analyze(
        store.root,
        tmp_path / "analysis",
        LinkOptions(
            session_id=session,
            flow_id="next",
            field="request.body#/token",
            direction="forward",
        ),
        multiprocessing.Event(),
        lambda _: None,
    )
    assert not any(edge["direction"] == "backward" for edge in forward["edges"])


def search(capture, query, **kwargs):
    """测试全文搜索不依赖起点字段，也不会写入抓包数据库。"""
    store, session, _, tmp_path = capture
    return analyze(
        store.root,
        tmp_path / "analysis",
        LinkOptions(
            session_id=session,
            flow_id="next",
            operation="search",
            query=query,
            **kwargs,
        ),
        multiprocessing.Event(),
        lambda _: None,
    )


def test_full_text_search_source_chronology_name_and_missing_origin(capture):
    result = search(capture, "secret-token-123", scan_limit=1, node_limit=1)
    assert result["scope"]["matched_requests"] == 4
    assert len(result["nodes"]) == 4  # 批次和字段追踪节点上限不截断全文搜索。
    assert any(
        edge["role"] == "source_candidate" and edge["from"] == "origin"
        for edge in result["edges"]
    )
    hits = sorted(
        (hit for node in result["nodes"] for hit in node["occurrences"]),
        key=lambda hit: hit["sequence"],
    )
    assert [hit["sequence"] for hit in hits] == list(range(1, len(hits) + 1))
    assert hits[0]["request_id"] == "origin"
    assert any(hit["field"] == "response.body" for hit in hits)
    assert "secret-token-123" not in json.dumps(result)
    assert search(capture, "token")["scope"]["matched_requests"] == 5
    missing = search(capture, "second-token")
    assert missing["source"]["matched"] is False
    assert missing["scope"]["matched_requests"] == 1
    assert {node["id"] for node in missing["nodes"]} == {"origin", "next"}
    scoped = search(capture, "token", request_ids=["contains"])
    assert scoped["scope"]["matched_requests"] == 1
    assert (
        search(capture, "token", target_filters={"search": "/unrelated"})["scope"][
            "matched_requests"
        ]
        == 1
    )


@pytest.mark.parametrize("encoding", ["identity", "gzip", "deflate", "br", "zstd"])
def test_search_full_plain_body_across_chunks_and_domains(capture, encoding):
    import gzip
    import zlib

    import brotli
    import zstandard

    store, session, _, _ = capture
    token = "这是跨块的-unique-secret"
    body = b"x" * 65531 + token.encode() + b"x" * 100000 + token.encode()
    packed = {
        "identity": lambda value: value,
        "gzip": gzip.compress,
        "deflate": zlib.compress,
        "br": brotli.compress,
        "zstd": zstandard.ZstdCompressor().compress,
    }[encoding](body)
    store.save_flow(
        session,
        {
            "id": "cross-domain",
            "host": "other.test",
            "url": "https://other.test/plain",
            "method": "GET",
            "status": "complete",
            "code": 200,
            "started": 95,
            "duration": 20,
            "request": {"headers": [["X-Unique-Name", "not-a-token"]], "body_b64": ""},
            "response": {
                "headers": [["Content-Encoding", encoding]],
                "body_b64": base64.b64encode(packed).decode(),
            },
        },
    )
    result = search(capture, token)
    hit = next(node for node in result["nodes"] if node["id"] == "cross-domain")[
        "occurrences"
    ][0]
    assert (
        hit["field"] == "response.body" and hit["count"] == 2 and hit["offset"] == 65531
    )
    assert result["scope"]["text_search_complete"] is True
    assert search(capture, "x-unique-name")["scope"]["matched_requests"] == 1
    assert search(capture, "other.test/plain")["scope"]["matched_requests"] == 1


def test_search_missing_body_warning_survives_incremental(capture):
    store, session, _, tmp_path = capture
    with store.connect(session) as db:
        detail = json.loads(
            db.execute("SELECT detail FROM flows WHERE id='origin'").fetchone()[0]
        )
    (store.root / session / "bodies" / detail["response"]["body_file"]).unlink()
    first = search(capture, "token")
    assert not first["scope"]["text_search_complete"]
    second = analyze(
        store.root,
        tmp_path / "analysis",
        LinkOptions(
            session_id=session, flow_id="next", operation="search", query="token"
        ),
        multiprocessing.Event(),
        lambda _: None,
        first,
    )
    assert not second["scope"]["text_search_complete"]
    assert second["scope"]["reused"] > 0


def test_search_results_pages_and_saved_query_reference(capture):
    store, session, _, tmp_path = capture

    async def scenario():
        manager = AnalysisManager(store.root, tmp_path / "analysis")
        try:
            job = await manager.start(
                LinkOptions(
                    session_id=session,
                    flow_id="next",
                    operation="search",
                    query="second-token",
                )
            )
            await manager.jobs[job["id"]]["task"]
            assert manager.status(job["id"])["status"] == "complete"
            first = manager.result(job["id"], 0, 1)
            second = manager.result(job["id"], 1, 1)
            assert first["nodes"][0]["id"] == "origin" and not first["edges"]
            assert second["nodes"][0]["id"] == "next" and not second["edges"]
            assert first["source_candidates"][0]["request_id"] == "origin"
            assert "second-token" not in json.dumps(first)
            restored = await manager.start(
                LinkOptions.model_validate(first["configuration"])
            )
            await manager.jobs[restored["id"]]["task"]
            assert manager.result(restored["id"])["scope"]["matched_requests"] == 1
        finally:
            await manager.close()

    asyncio.run(scenario())


def test_search_field_value_change_invalidates_incremental_matches(capture):
    store, session, save, tmp_path = capture
    options = LinkOptions(
        session_id=session,
        flow_id="origin",
        operation="search",
        field="response.body#/token",
    )
    first = analyze(
        store.root,
        tmp_path / "analysis",
        options,
        multiprocessing.Event(),
        lambda _: None,
    )
    save("origin", 100, token="a-brand-new-value", response=True)
    second = analyze(
        store.root,
        tmp_path / "analysis",
        options,
        multiprocessing.Event(),
        lambda _: None,
        first,
    )
    assert second["scope"]["matched_requests"] == 1
    assert second["scope"]["reused"] == 0


def test_text_search_cancellation_and_capture_truncation(capture):
    from capture.backend.links.text import message_matches

    store, session, _, _ = capture
    folder = store.root / session
    flow = {
        "id": "truncated",
        "started": 100,
        "url": "",
        "request": {"headers": [], "body_text": "search-token", "truncated": True},
    }
    hits, warnings = message_matches(folder, flow, "token", multiprocessing.Event())
    assert hits and warnings
    cancelled = multiprocessing.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        message_matches(folder, flow, "token", cancelled)


def test_response_source_is_ranked_and_linked_by_full_value(capture):
    """目标请求携带完整值时，先找到响应来源，再显示普通先后使用。"""
    result = search(capture, "secret-token-123")
    top = result["source_candidates"][0]
    assert top["request_id"] == "origin"
    assert top["field"] == "response.body#/token"
    assert top["matches_reference_value"] and top["later_requests"] >= 1
    assert any(
        edge["from"] == "origin"
        and edge["to"] == "next"
        and edge["source_field"] == "response.body#/token"
        and edge["role"] == "source_candidate"
        for edge in result["edges"]
    )
    assert all(edge["relation"] != "contains" for edge in result["edges"])
    assert "secret-token-123" not in json.dumps(result)
