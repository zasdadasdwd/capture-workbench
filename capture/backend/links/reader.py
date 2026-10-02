"""独立只读连接与派生字段缓存；不会使用抓包 Store 的线程锁。"""

import hashlib
import json
import sqlite3
import zlib
from pathlib import Path

import brotli
import zstandard

from capture.backend.analysis.parameters import extract_parameters
from capture.backend.filters import build_conditions
from capture.backend.storage import decode_body


class CaptureReader:
    """分批读取 SQLite 元数据，结束查询后再读正文和解析字段。"""

    def __init__(self, root: Path, session_id: str, indexes: Path):
        folder = (root / session_id).resolve()
        if folder.parent != root.resolve() or not folder.is_dir():
            raise FileNotFoundError("会话不存在")
        self.folder = folder
        self.db = sqlite3.connect(
            (folder / "capture.sqlite").as_uri() + "?mode=ro", uri=True, timeout=0.2
        )
        self.db.execute("PRAGMA query_only=ON")
        self.db.row_factory = sqlite3.Row
        indexes.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = indexes / (
            hashlib.sha256(session_id.encode()).hexdigest()[:24] + ".sqlite"
        )
        self.cache = sqlite3.connect(path)
        path.chmod(0o600)
        self.cache.execute(
            "CREATE TABLE IF NOT EXISTS fields (id TEXT PRIMARY KEY, stamp TEXT, value TEXT, updated REAL)"
        )

    def close(self):
        self.db.close()
        self.cache.close()

    def load(self, flow_id):
        """每次最多解码 64 KiB；不把原始 Base64 搬进分析进程结果。"""
        row = self.db.execute(
            "SELECT detail, status FROM flows WHERE id=?", (flow_id,)
        ).fetchone()
        if row is None:
            raise FileNotFoundError("请求已删除或不存在")
        flow = json.loads(row["detail"])
        flow["status"] = row["status"]
        stamps = [row["detail"], row["status"]]
        for part in ("request", "original_request", "response"):
            message = flow.get(part)
            if not message or not message.get("body_file"):
                continue
            filename = message["body_file"]
            if Path(filename).name != filename:
                raise ValueError("正文路径无效")
            path = self.folder / "bodies" / filename
            try:
                stat = path.stat()
                stamps.append(f"{stat.st_size}:{stat.st_mtime_ns}")
                encoding = next(
                    (
                        v.lower()
                        for k, v in message.get("headers", [])
                        if k.lower() == "content-encoding"
                    ),
                    "identity",
                )
                with path.open("rb") as stream:
                    raw = stream.read(
                        65537 if encoding in ("", "identity") else 16 * 1024 * 1024 + 1
                    )
                if len(raw) > 16 * 1024 * 1024:
                    message["display_truncated"] = True
                    message["body_text"] = ""
                    continue
                decoded = decode_body(raw, encoding, 65536)
                message["display_truncated"] = len(decoded) > 65536
                message["body_text"] = decoded[:65536].decode("utf-8", errors="replace")
            except FileNotFoundError:
                message["decode_error"] = True
                message["body_text"] = ""
                stamps.append("deleted")
            except (
                ValueError,
                TypeError,
                EOFError,
                OSError,
                zlib.error,
                brotli.error,
                zstandard.ZstdError,
            ):
                message["decode_error"] = True
                message["body_text"] = ""
        stamp = hashlib.sha256("\0".join(stamps).encode()).hexdigest()
        return flow, stamp

    def fields(self, flow_id):
        """字段缓存只存派生索引，抓包数据库始终只读。"""
        flow, stamp = self.load(flow_id)
        row = self.cache.execute(
            "SELECT value FROM fields WHERE id=? AND stamp=?", (flow_id, stamp)
        ).fetchone()
        if row:
            return flow, json.loads(row[0])
        extracted = extract_parameters(flow)
        self.cache.execute(
            "INSERT OR REPLACE INTO fields VALUES (?, ?, ?, strftime('%s','now'))",
            (flow_id, stamp, json.dumps(extracted, ensure_ascii=False)),
        )
        self.cache.commit()
        return flow, extracted

    def after(
        self,
        started,
        ended,
        limit,
        host="",
        overlap=False,
        request_ids=None,
        target_filters=None,
        descending=False,
    ):
        """只返回限定时间窗的稳定摘要列表，查询游标立即释放。"""
        clause = " AND host=?" if host else ""
        parameters = [started, ended, *([host] if host else [])]
        if target_filters is not None:
            conditions, bindings = build_conditions(target_filters)
            if conditions:
                clause += " AND (" + conditions.removeprefix("WHERE ") + ")"
                parameters.extend(bindings)
        if request_ids is not None:
            if not request_ids:
                return [], False
            clause += " AND id IN (" + ",".join("?" for _ in request_ids) + ")"
            parameters.extend(request_ids)
        rows = self.db.execute(
            "SELECT id, host, url, method, status, code, started, duration, size FROM flows WHERE started>=? AND started<=?"
            + clause
            + (
                " ORDER BY started DESC, id DESC LIMIT ?"
                if descending
                else " ORDER BY started, id LIMIT ?"
            ),
            [*parameters, limit + 1],
        ).fetchall()
        return [dict(row) for row in rows[:limit]], len(rows) > limit

    def signature(self, flow_id):
        row = self.db.execute(
            "SELECT detail,status FROM flows WHERE id=?", (flow_id,)
        ).fetchone()
        if row is None:
            return None
        stamps = [row[0], row[1]]
        flow = json.loads(row[0])
        for part in ("request", "original_request", "response"):
            filename = flow.get(part, {}).get("body_file")
            if not filename:
                continue
            if Path(filename).name != filename:
                raise ValueError("正文路径无效")
            try:
                stat = (self.folder / "bodies" / filename).stat()
                stamps.append(f"{stat.st_size}:{stat.st_mtime_ns}")
            except FileNotFoundError:
                stamps.append("deleted")
        return hashlib.sha256("\0".join(stamps).encode()).hexdigest()

    def search_rows(self, options, started):
        """全文搜索分批遍历指定范围，批大小不限制最终匹配请求数。"""
        clauses, bindings = [], []
        if options.host:
            clauses.append("host=?")
            bindings.append(options.host)
        if options.target_filters:
            condition, values = build_conditions(options.target_filters)
            if condition:
                clauses.append(condition.removeprefix("WHERE "))
                bindings.extend(values)
        if options.request_ids is not None:
            if not options.request_ids:
                return
            clauses.append("id IN (" + ",".join("?" for _ in options.request_ids) + ")")
            bindings.extend(options.request_ids)
        if options.direction != "both":
            clauses.append(
                "started" + ("<=?" if options.direction == "backward" else ">=?")
            )
            bindings.append(started)
        where = (
            " WHERE " + " AND ".join("(" + clause + ")" for clause in clauses)
            if clauses
            else ""
        )
        cursor = self.db.execute(
            "SELECT id,started,duration FROM flows" + where + " ORDER BY started,id",
            bindings,
        )
        try:
            while batch := cursor.fetchmany(min(options.scan_limit, 500)):
                for row in batch:
                    yield dict(row)
        finally:
            cursor.close()
