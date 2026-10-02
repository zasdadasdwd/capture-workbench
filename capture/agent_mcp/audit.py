"""开发者查询日志：滚动 JSONL，记录请求标识，避免保存敏感报文。"""

import functools
import hashlib
import inspect
import json
import logging
import os
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo


class AuditLog:
    """记录工具调用及返回请求 ID，stdio 标准输出不写任何日志。"""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(f"capture.mcp.audit.{uuid4().hex}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.handler = RotatingFileHandler(
            path, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        self.logger.addHandler(self.handler)

    def close(self):
        self.handler.close()
        self.logger.removeHandler(self.handler)

    @staticmethod
    def safe(value, key=""):
        """参数值、搜索词及编辑报文只记长度和摘要，保留定位字段与过滤条件。"""
        if hasattr(value, "model_dump"):
            value = value.model_dump(exclude_none=True)
        if (
            key
            in {
                "value",
                "search",
                "query",
                "url",
                "headers",
                "body_text",
                "body",
                "body_b64",
                "edit",
            }
            and value is not None
        ):
            text = json.dumps(value, ensure_ascii=False)
            return {
                "redacted": True,
                "length": len(text),
                "sha256": hashlib.sha256(text.encode()).hexdigest()[:16],
            }
        if isinstance(value, dict):
            return {name: AuditLog.safe(item, name) for name, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [AuditLog.safe(item) for item in value[:100]]
        return value

    def wrap(self, function):
        """保留工具签名；成功与失败都写一条日志，不记录响应正文。"""

        @functools.wraps(function)
        async def audited(*args, **kwargs):
            call_id = uuid4().hex
            started = time.monotonic()
            parameters = inspect.signature(function).bind(*args, **kwargs)
            parameters.apply_defaults()
            record = {
                "process_id": os.getpid(),
                "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "call_id": call_id,
                "tool": function.__name__,
                "arguments": self.safe(dict(parameters.arguments)),
            }
            try:
                result = await function(*args, **kwargs)
                internal = result.pop("_audit", {})
                record["status"] = "ok"
                record["returned_request_ids"] = [
                    item["id"]
                    for item in result.get("items", result.get("nodes", []))
                    if "id" in item
                ]
                record["result_session_id"] = result.get("session_id")
                record["scanned"] = result.get(
                    "scanned", result.get("scope", {}).get("scanned")
                )
                record["read_request_ids"] = internal.get(
                    "read_request_ids",
                    result.get("scope", {}).get("scanned_request_ids", []),
                )
                record["related_request_ids"] = list(
                    dict.fromkeys(
                        [item["flow_id"] for item in result.get("candidates", [])]
                        + [
                            item["from"]
                            for item in result.get("candidate_relations", [])
                        ]
                    )
                )
                return result
            except Exception as exc:
                record.update(status="error", error_type=type(exc).__name__)
                raise
            finally:
                record["duration_ms"] = round((time.monotonic() - started) * 1000, 2)
                self.logger.info(json.dumps(record, ensure_ascii=False))

        return audited
