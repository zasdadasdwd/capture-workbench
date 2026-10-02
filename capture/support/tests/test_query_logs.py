"""查询日志尾部读取：滚动、损坏行和资源上限。"""

import json

from capture.backend.query_logs import QueryLogs


def test_log_rotation_sources_and_partial_lines(tmp_path):
    def entry(id, timestamp):
        return json.dumps({"call_id": id, "timestamp": timestamp}) + "\n"

    (tmp_path / "mcp.jsonl.1").write_text(entry("old", "2026-01-01"))
    (tmp_path / "mcp.jsonl").write_text(
        "broken\n" + entry("new", "2026-03-01") + '{"unfinished":'
    )
    (tmp_path / "analysis.jsonl").write_text(entry("analysis", "2026-02-01"))
    logs = QueryLogs(tmp_path)
    result = logs.read(2)
    assert [item["call_id"] for item in result["items"]] == ["new", "analysis"]
    assert [item["source"] for item in result["items"]] == ["mcp", "analysis_api"]
    assert result["limited"]
    revision = logs.revision()
    with (tmp_path / "mcp.jsonl").open("a") as stream:
        stream.write("changed")
    assert logs.revision() != revision


def test_log_read_is_bounded_and_empty(tmp_path):
    logs = QueryLogs(tmp_path)
    assert logs.read()["items"] == []
    (tmp_path / "mcp.jsonl").write_bytes(
        b"x" * (3 * 1024 * 1024) + b'\n{"timestamp":"now","tool":"search_requests"}\n'
    )
    result = logs.read()
    assert result["limited"]
    assert len(result["items"]) == 1
    assert result["items"][0]["tool"] == "search_requests"


def test_large_mcp_tail_does_not_hide_current_analysis(tmp_path):
    (tmp_path / "mcp.jsonl").write_bytes(
        b"x" * (3 * 1024 * 1024) + b'\n{"timestamp":"2026-01-01","call_id":"mcp"}\n'
    )
    (tmp_path / "analysis.jsonl").write_text(
        '{"timestamp":"2026-02-01","call_id":"analysis"}\n'
    )

    result = QueryLogs(tmp_path).read()

    assert [(item["call_id"], item["source"]) for item in result["items"]] == [
        ("analysis", "analysis_api"),
        ("mcp", "mcp"),
    ]
    assert result["limited"]


def test_budget_covers_each_existing_log_tail(tmp_path):
    for name in ("mcp", "analysis"):
        for suffix in ("", ".1", ".2", ".3"):
            call_id = f"{name}{suffix}"
            (tmp_path / f"{name}.jsonl{suffix}").write_bytes(
                b"x" * (512 * 1024)
                + b"\n"
                + json.dumps({"timestamp": call_id, "call_id": call_id}).encode()
                + b"\n"
            )

    result = QueryLogs(tmp_path).read()

    assert {item["call_id"] for item in result["items"]} == {
        f"{name}{suffix}"
        for name in ("mcp", "analysis")
        for suffix in ("", ".1", ".2", ".3")
    }
    assert result["limited"]


def test_unchanged_logs_share_snapshot_and_refresh_on_append(tmp_path, monkeypatch):
    """多个订阅者只解析一次，追加记录后缓存失效。"""
    path = tmp_path / "mcp.jsonl"
    path.write_text('{"timestamp":"1","call_id":"old"}\n')
    logs = QueryLogs(tmp_path)
    original = logs.read_tail
    reads = []

    def read():
        reads.append(True)
        return original()

    monkeypatch.setattr(logs, "read_tail", read)
    assert logs.read(1)["items"][0]["call_id"] == "old"
    logs.read(100)
    assert len(reads) == 1
    with path.open("a") as stream:
        stream.write('{"timestamp":"2","call_id":"new"}\n')
    assert logs.read(1)["items"][0]["call_id"] == "new"
    assert len(reads) == 2
