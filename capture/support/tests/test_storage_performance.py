"""性能改动的行为验证：缓存有界、并发安全、预览不改变原始数据。"""

import base64
import gzip
import zlib
from concurrent.futures import ThreadPoolExecutor

import brotli
import pytest
import zstandard

from capture.backend.storage import Store, decode_body
from config import Settings


def test_preview_is_bounded_and_raw_body_is_preserved(tmp_path):
    """大压缩正文只展示 64 KiB，完整导出仍读取原始压缩字节。"""
    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())
    original = b"a" * 200_000
    compressed = gzip.compress(original)
    record = {
        "id": "large",
        "status": "complete",
        "response": {
            "headers": [["Content-Encoding", "gzip"]],
            "body_b64": base64.b64encode(compressed).decode(),
            "body_size": len(compressed),
            "truncated": False,
        },
    }
    store.save_flow(session, record)
    preview = store.get_flow(session, "large", preview=True)["response"]
    assert len(preview["body_text"]) == 64 * 1024
    assert preview["display_truncated"]
    assert "body_b64" not in preview and "decoded_b64" not in preview
    full = store.get_flow(session, "large")["response"]
    assert base64.b64decode(full["body_b64"]) == compressed
    assert full["body_text"] == original.decode()
    assert "body_b64" in record["response"]  # 保存不修改调用者对象。
    store.close()


def test_empty_bodies_cached_counts_and_concurrent_writes(tmp_path):
    """跨线程共用连接时数据完整；空正文不创建文件，会话缓存及时失效。"""
    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())
    assert store.sessions()[0]["count"] == 0

    def write(index):
        store.save_flow(
            session,
            {
                "id": str(index),
                "status": "complete",
                "started": index,
                "request": {"body_b64": "", "headers": []},
                "response": {"body_b64": "", "headers": []},
            },
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(100)))
    assert store.list_flows(session)["total"] == 100
    assert store.sessions()[0]["count"] == 100
    assert list((store.root / session / "bodies").iterdir()) == []
    store.finish(session)
    assert store.sessions()[0]["status"] == "stopped"
    for _ in range(10):
        store.create_session(Settings().model_dump())
    assert len(store.connections) <= 8
    store.close()
    assert len(store.connections) == 0


@pytest.mark.parametrize(
    "encoding,compress",
    [
        ("gzip", gzip.compress),
        ("br", brotli.compress),
        ("deflate", zlib.compress),
        ("zstd", zstandard.ZstdCompressor().compress),
    ],
)
def test_compressed_preview_limits(encoding, compress):
    """常见压缩格式的预览均有界，不会因正文解压膨胀而传输大段文本。"""
    content = b"x" * 200_000
    assert (
        decode_body(compress(content), encoding, 64 * 1024) == content[: 64 * 1024 + 1]
    )


def test_corrupt_compression_can_still_be_inspected(tmp_path):
    """损坏的编码数据不应令详情 API 报错，原始字节仍可读取。"""
    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())
    store.save_flow(
        session,
        {
            "id": "bad",
            "status": "complete",
            "response": {
                "headers": [["Content-Encoding", "deflate"]],
                "body_b64": "aW52YWxpZA==",
            },
        },
    )
    assert store.get_flow(session, "bad", preview=True)["response"]["decode_error"]
    store.close()


def test_directory_cache_follows_lifecycle_moves_and_deletion(tmp_path):
    """同一请求的响应更新不重复计数，修改 URL 和删除即时反映。"""
    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())
    assert store.directories(session) == []
    store.save_flow(
        session,
        {"id": "a", "host": "example.test", "url": "https://example.test/a?one=1"},
    )
    store.save_flow(
        session,
        {"id": "b", "host": "example.test", "url": "https://example.test/a?two=2"},
    )
    store.save_flow(session, {"id": "a", "status": "complete", "code": 200})
    assert store.directories(session) == [
        {"host": "example.test", "path": "/a", "count": 2}
    ]
    store.save_flow(session, {"id": "a", "url": "https://example.test/b"})
    assert {item["path"]: item["count"] for item in store.directories(session)} == {
        "/a": 1,
        "/b": 1,
    }
    store.delete_flows(session, ["b"])
    assert store.directories(session) == [
        {"host": "example.test", "path": "/b", "count": 1}
    ]
    # 调用方不能通过修改返回列表破坏内部计数。
    store.directories(session)[0]["count"] = 999
    assert store.directories(session)[0]["count"] == 1
    store.delete_flows(session)
    assert store.directories(session) == []
    store.close()


def test_identity_preview_reads_only_display_limit(tmp_path, monkeypatch):
    """预览不全量读取正文；完整详情仍保留所有原始字节。"""
    from pathlib import Path

    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())
    raw = b"a" * (2 * 1024 * 1024)
    store.save_flow(
        session,
        {
            "id": "large",
            "response": {"headers": [], "body_b64": base64.b64encode(raw).decode()},
        },
    )
    original = Path.open
    sizes = []

    class Reader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def read(self, size=-1):
            sizes.append(size)
            return self.stream.read(size)

    def open_file(path, *args, **kwargs):
        stream = original(path, *args, **kwargs)
        return Reader(stream) if path.suffix == ".bin" else stream

    monkeypatch.setattr(Path, "open", open_file)
    preview = store.get_flow(session, "large", preview=True)["response"]
    assert sizes == [65537]
    assert preview["display_truncated"] and len(preview["body_text"]) == 65536
    full = store.get_flow(session, "large")["response"]
    assert base64.b64decode(full["body_b64"]) == raw
    store.close()


def test_full_text_view_omits_duplicate_encodings(tmp_path):
    """完整文本与原始模式的展示一致；重放默认仍能取到原始正文。"""
    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())
    raw = gzip.compress("中文内容".encode() * 1000)
    store.save_flow(
        session,
        {
            "id": "text",
            "response": {
                "headers": [["Content-Encoding", "gzip"]],
                "body_b64": base64.b64encode(raw).decode(),
            },
        },
    )
    text = store.get_flow(session, "text", include_raw=False)["response"]
    full = store.get_flow(session, "text")["response"]
    assert text["body_text"] == full["body_text"]
    assert "body_b64" not in text and "decoded_b64" not in text
    assert base64.b64decode(full["body_b64"]) == raw
    store.close()
