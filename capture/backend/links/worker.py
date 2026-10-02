"""子进程分析入口：字段发现、数据值匹配与实时增量复核。"""

import hashlib
import os
import time
from pathlib import Path
from queue import Full
from urllib.parse import urlsplit

from capture.backend.analysis.parameters import select_parameter, value_variants
from capture.backend.links.models import LinkOptions
from capture.backend.links.reader import CaptureReader


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def masked(value):
    """前端默认只收到长度和指纹，原值由显式 reveal 查询提供。"""
    return f"•••• · {len(value)} 字符 · {digest(value)[:10]}"


def variants(value, options):
    """有限的声明式转换；转换吻合不代表已确认客户端算法。"""
    result = value_variants(value) if options.transforms else {value: "原值"}
    for rule in options.rules:
        if rule.operation in ("sha256", "sha1", "md5"):
            transformed = hashlib.new(rule.operation, value.encode()).hexdigest()
        elif rule.operation == "hex":
            transformed = value.encode().hex()
        elif rule.prefix and value.startswith(rule.prefix):
            transformed = value[len(rule.prefix) :]
        else:
            continue
        result[transformed] = f"规则：{rule.name}"
    return result


def summary(flow):
    return {
        key: flow.get(key)
        for key in (
            "id",
            "host",
            "url",
            "method",
            "status",
            "code",
            "started",
            "duration",
        )
    }


def analyze(root, directory, options, cancelled, report, previous=None):
    """使用只读连接，返回有界图及可复核的字段级证据。"""
    reader = CaptureReader(Path(root), options.session_id, Path(directory) / "indexes")
    try:
        if options.operation == "search":
            from capture.backend.links.search import search_occurrences

            if not options.query and options.field:
                _, fields = reader.fields(options.flow_id)
                value = select_parameter(fields["fields"], options.field)["value"]
                if options.value_range:
                    begin, end = options.value_range
                    if end > len(value):
                        raise ValueError("参数内容已改变，请重新选择分析数据")
                    value = value[begin:end]
                options = options.model_copy(update={"query": value})
            return search_occurrences(reader, options, cancelled, report, previous)
        seed, extracted = reader.fields(options.flow_id)
        if options.operation == "fields":
            items = []
            query = options.query.casefold()
            for item in extracted["fields"]:
                field, value = item["field"], item["value"]
                haystack = (
                    field
                    if options.query_scope == "field"
                    else value
                    if options.query_scope == "value"
                    else field + "\n" + value
                )
                if query and query not in haystack.casefold():
                    continue
                items.append(
                    {
                        "field": field,
                        "value": value if options.reveal else masked(value),
                        "length": len(value),
                        "fingerprint": digest(value),
                        "common": len(value) < 4
                        or value.casefold() in ("true", "false", "null", "none"),
                    }
                )
            return {
                "operation": "fields",
                "session_id": options.session_id,
                "flow_id": options.flow_id,
                "items": items,
                "warnings": extracted["warnings"],
                "scope": {
                    "body_preview_bytes": 65536,
                    "total_fields": len(extracted["fields"]),
                    "matched_fields": len(items),
                },
            }
        selected = select_parameter(extracted["fields"], options.field)
        full_value = selected["value"]
        value = full_value
        if options.value_range:
            begin, end = options.value_range
            if end > len(value):
                raise ValueError("参数内容已改变，请重新选择分析数据")
            value = value[begin:end]
        if not options.allow_common and (
            len(value) < 4 or value.casefold() in ("true", "false", "null", "none")
        ):
            raise ValueError("短值或常见值容易误匹配，请显式开启常见值追踪")
        start = seed.get("started", 0)
        response_source = selected["field"].startswith("response.")
        if response_source and seed.get("status") != "complete":
            raise ValueError("响应尚未完整结束，请等待完成后追踪")
        available = (
            start + (seed.get("duration") or 0) / 1000 if response_source else start
        )
        # 前向从数据可用时间查使用者，后向从请求开始时间查更早的来源候选。
        query_scope = {
            "request_ids": options.request_ids,
            "target_filters": options.target_filters,
        }
        before, after = [], []
        before_limited = after_limited = False
        if options.direction in ("both", "backward"):
            before, before_limited = reader.after(
                max(0, start - options.window_seconds),
                start - 0.000001,
                options.scan_limit,
                options.host,
                descending=True,
                **query_scope,
            )
        if options.direction in ("both", "forward"):
            after, after_limited = reader.after(
                start if options.include_overlap else available,
                available + options.window_seconds,
                options.scan_limit,
                options.host,
                **query_scope,
            )
        # 两侧交替取最近的请求，避免较多历史记录耗尽整个扫描预算。
        rows = []
        for index in range(max(len(before), len(after))):
            for side in (before, after):
                if index < len(side):
                    rows.append(side[index])
        scan_limited = before_limited or after_limited or len(rows) > options.scan_limit
        rows = rows[: options.scan_limit]
        source_variants = variants(value, options)
        full_variants = variants(full_value, options)
        signatures = {}
        matches = {}
        old_signatures = (previous or {}).get("_signatures", {})
        old_matches = (previous or {}).get("_matches", {})
        seed_signature = reader.signature(options.flow_id)
        reuse = (previous or {}).get("_seed_signature") == seed_signature
        warnings = list(extracted["warnings"])
        missing = []
        parsed = 0
        cached = 0
        for index, row in enumerate(rows):
            if cancelled.is_set():
                raise InterruptedError("分析已取消")
            if row["id"] == options.flow_id:
                continue
            report({"scanned": index + 1, "total": len(rows), "matched": len(matches)})
            signature = reader.signature(row["id"])
            if signature is None:
                missing.append(row["id"])
                continue
            signatures[row["id"]] = signature
            if reuse and old_signatures.get(row["id"]) == signature:
                if row["id"] in old_matches:
                    matches[row["id"]] = old_matches[row["id"]]
                cached += 1
                continue
            try:
                flow, fields = reader.fields(row["id"])
            except FileNotFoundError:
                missing.append(row["id"])
                continue
            parsed += 1
            warnings.extend(fields["warnings"])
            edges = []
            backward = row["started"] < start
            for item in fields["fields"]:
                candidate_response = item["field"].startswith("response.")
                if not item["field"].startswith(("request.", "response.")) or (
                    not backward and candidate_response
                ):
                    continue
                candidate_available = row["started"] + (
                    (row.get("duration") or 0) / 1000 if candidate_response else 0
                )
                if (
                    backward
                    and candidate_response
                    and (row["status"] != "complete" or candidate_available > start)
                    and not options.include_overlap
                ):
                    continue
                candidate = item["value"]
                target_variants = variants(candidate, options)
                common = source_variants.keys() & target_variants.keys()
                relation = "exact" if candidate == value else "transform"
                canonical = (
                    value if value in common else min(common) if common else None
                )
                if canonical is None:
                    if not options.contains or len(value) < 4 or value not in candidate:
                        continue
                    relation = "contains"
                evidence = {
                    "id": digest(
                        options.flow_id + selected["field"] + row["id"] + item["field"]
                    )[:24],
                    "from": options.flow_id,
                    "to": row["id"],
                    "source_field": selected["field"],
                    "target_field": item["field"],
                    "relation": relation,
                    "full_value_match": bool(
                        full_variants.keys() & target_variants.keys()
                    ),
                    "partial_selection": options.value_range is not None,
                    "source_transform": source_variants.get(canonical, "原值包含"),
                    "target_transform": target_variants.get(canonical, "原值包含"),
                    "fingerprint": digest(value),
                    "target_fingerprint": digest(candidate),
                    "value": masked(value),
                    "target_value": masked(candidate),
                    "time_delta_ms": round((row["started"] - available) * 1000, 2),
                    "available_before_target": row["started"] >= available,
                    "causality": "unconfirmed",
                }
                evidence["related_request_id"] = row["id"]
                evidence["direction"] = "backward" if backward else "forward"
                evidence["role"] = (
                    ("source_candidate" if candidate_response else "earlier_use")
                    if backward
                    else "later_use"
                )
                if backward:
                    evidence.update(
                        **{"from": row["id"], "to": options.flow_id},
                        source_field=item["field"],
                        target_field=selected["field"],
                        source_transform=target_variants.get(canonical, "包含选中片段"),
                        target_transform=source_variants.get(canonical, "选中片段"),
                        fingerprint=digest(candidate),
                        target_fingerprint=digest(value),
                        value=masked(candidate),
                        target_value=masked(value),
                        time_delta_ms=round((start - candidate_available) * 1000, 2),
                        available_before_target=candidate_available <= start,
                    )
                edges.append(evidence)
                if len(edges) >= 20:
                    warnings.append("单个请求最多显示 20 条字段匹配证据")
                    break
            if edges:
                matches[row["id"]] = {"node": summary(flow), "edges": edges}
        ordered = [matches[row["id"]] for row in rows if row["id"] in matches]
        visible = ordered[: options.node_limit]
        nodes = [
            {
                **summary(seed),
                "source": True,
                "field": selected["field"],
                "fingerprint": digest(value),
                "value": masked(value),
            }
        ] + [item["node"] for item in visible]
        edges = [edge for item in visible for edge in item["edges"]]
        groups = {}
        for item in ordered:
            node = item["node"]
            path = urlsplit(node["url"] or "").path
            key = (node["host"], path)
            group = groups.setdefault(
                key, {"host": node["host"], "path": path, "count": 0}
            )
            group["count"] += 1
        return {
            "operation": "trace",
            "session_id": options.session_id,
            "source": nodes[0],
            "nodes": nodes,
            "edges": edges,
            "groups": list(groups.values()),
            "scope": {
                "scanned": len(signatures),
                "parsed": parsed,
                "reused": cached,
                "matched_requests": len(matches),
                "displayed_requests": len(visible),
                "scan_limit_reached": scan_limited,
                "node_limit_reached": len(ordered) > options.node_limit,
                "window_seconds": options.window_seconds,
                "direction": options.direction,
                "source_candidates": sum(
                    any(edge["role"] == "source_candidate" for edge in item["edges"])
                    for item in ordered
                ),
                "earlier_uses": sum(
                    any(edge["role"] == "earlier_use" for edge in item["edges"])
                    for item in ordered
                ),
                "later_uses": sum(
                    any(edge["role"] == "later_use" for edge in item["edges"])
                    for item in ordered
                ),
                "start": max(0, start - options.window_seconds)
                if options.direction != "forward"
                else available,
                "end": available + options.window_seconds,
                "body_preview_bytes": 65536,
                "missing_requests": missing,
                "scanned_request_ids": list(signatures),
            },
            "warnings": list(dict.fromkeys(warnings))[:30]
            + ["值匹配支持候选关系，不能证明客户端调用依赖。未匹配不代表不存在关联。"],
            "_signatures": signatures,
            "_matches": matches,
            "_seed_signature": seed_signature,
        }
    finally:
        reader.close()


def run_worker(root, directory, payload, cancelled, queue, previous=None):
    """每个任务独立进程，低优先级、内存上限与超时由父进程共同约束。"""
    try:
        if hasattr(os, "nice"):
            os.nice(10)
        try:
            import resource

            resource.setrlimit(
                resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024)
            )
        except (ImportError, ValueError, OSError):
            pass
        last = 0

        def report(progress):
            nonlocal last
            now = time.monotonic()
            if now - last < 0.15:
                return
            last = now
            try:
                queue.put_nowait({"type": "progress", **progress})
            except Full:
                pass

        result = analyze(
            root,
            directory,
            LinkOptions.model_validate(payload),
            cancelled,
            report,
            previous,
        )
        queue.put({"type": "result", "result": result}, timeout=5)
    except InterruptedError:
        queue.put({"type": "cancelled"}, timeout=5)
    except Exception as exc:  # noqa: BLE001 — 子进程边界必须将故障转成任务状态
        # 不把正文或参数值写进错误日志。
        message = (
            str(exc)[:200]
            if isinstance(exc, (ValueError, FileNotFoundError))
            else "分析失败：" + type(exc).__name__
        )
        queue.put({"type": "error", "error": message}, timeout=5)
