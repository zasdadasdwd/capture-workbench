"""使用临时数据库测量热路径，绝不写入真实抓包会话。"""

import argparse
import base64
import json
import statistics
import tempfile
import time
import tracemalloc
from pathlib import Path

from capture.backend.storage import Store
from config import Settings


def measure(function, repeats=12):
    """记录中位数与 Python 分配峰值，首次调用也计入样本。"""
    samples = []
    tracemalloc.start()
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append((time.perf_counter() - started) * 1000)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "median_ms": round(statistics.median(samples), 3),
        "first_ms": round(samples[0], 3),
        "peak_kib": round(peak / 1024, 1),
    }


def main():
    """构造 5 万条 URL 和 8 MiB 未压缩响应，覆盖目录及详情预览。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder))
        session = store.create_session(Settings().model_dump())
        with store.connect(session) as db:
            db.executemany(
                "INSERT INTO flows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    (
                        str(i),
                        "example.test",
                        f"https://example.test/catalog/{i % 20}?item={i}",
                        "GET",
                        "complete",
                        200,
                        i,
                        1,
                        0,
                        "capture",
                        "{}",
                    )
                    for i in range(50000)
                ),
            )
        store.save_flow(
            session,
            {
                "id": "large",
                "response": {
                    "headers": [],
                    "body_b64": base64.b64encode(b"a" * (8 * 1024 * 1024)).decode(),
                },
            },
        )
        result = {
            "rows": 50000,
            "body_bytes": 8 * 1024 * 1024,
            "directories": measure(lambda: store.directories(session)),
            "preview": measure(lambda: store.get_flow(session, "large", preview=True)),
        }
        result["full_view"] = {
            "raw_json_bytes": len(
                json.dumps(store.get_flow(session, "large")).encode()
            ),
            "text_json_bytes": len(
                json.dumps(store.get_flow(session, "large", include_raw=False)).encode()
            ),
        }
        store.close()
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
