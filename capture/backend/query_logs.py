"""读取有界日志尾部，供开发者页面实时观察 Agent 查询。"""

import json
import threading
from pathlib import Path


class QueryLogs:
    """只读取固定日志文件的尾部，避免日志增长后拖慢抓包。"""

    def __init__(self, directory: Path):
        self.directory = directory
        self.lock = threading.Lock()
        self.cached_revision = None
        self.cached_snapshot = None

    def paths(self):
        return [
            self.directory / f"{name}.jsonl{suffix}"
            for name in ("mcp", "analysis")
            for suffix in ("", ".1", ".2", ".3")
        ]

    def revision(self):
        """文件大小与修改时间用于检测写入和日志滚动。"""
        result = []
        for path in self.paths():
            try:
                stat = path.stat()
                result.append((path.name, stat.st_ino, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                pass
        return result

    def read(self, limit=100):
        """同一服务的多个订阅者共享快照；未变化的文件无需反复解析。"""
        with self.lock:
            revision = self.revision()
            if revision != self.cached_revision or self.cached_snapshot is None:
                self.cached_snapshot = self.read_tail()
                self.cached_revision = revision
            snapshot = self.cached_snapshot
            return {
                "items": snapshot["items"][:limit],
                "limited": snapshot["limited"] or len(snapshot["items"]) > limit,
                "limit": limit,
            }

    def read_tail(self):
        """最多读 2 MiB 尾部；丢弃未写完或损坏的行，返回最新记录。"""
        records = []
        budget = 2 * 1024 * 1024
        limited = False
        files = []
        for path in self.paths():
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                continue
            weight = 2 if path.name.endswith(".jsonl") else 1
            files.append((path, size, weight))

        # 每份现存日志先保留尾部额度；当前日志比滚动历史多一份。
        total_weight = sum(weight for _, _, weight in files)
        counts = {
            path: min(size, budget * weight // total_weight)
            for path, size, weight in files
        }
        remaining = budget - sum(counts.values())
        # 小文件用不完的额度先补给当前日志。
        for path, size, _ in sorted(
            files, key=lambda file: not file[0].name.endswith(".jsonl")
        ):
            extra = min(size - counts[path], remaining)
            counts[path] += extra
            remaining -= extra

        for path, _, _ in files:
            try:
                with path.open("rb") as stream:
                    size = stream.seek(0, 2)
                    count = min(size, counts[path])
                    if count == 0:
                        limited |= size > 0
                        continue
                    start = size - count
                    stream.seek(start)
                    data = stream.read(count)
                    limited |= start > 0
                    if start:
                        data = data.partition(b"\n")[2]
                    lines = data.split(b"\n")[:-1]
                    for line in lines:
                        try:
                            record = json.loads(line)
                            if not isinstance(record, dict):
                                continue
                            record["source"] = (
                                "mcp"
                                if path.name.startswith("mcp.")
                                else "analysis_api"
                            )
                            records.append(record)
                        except (ValueError, UnicodeDecodeError):
                            continue
            except FileNotFoundError:
                continue
        records.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return {
            "items": records[:100],
            "limited": limited or len(records) > 100,
            "limit": 100,
        }
