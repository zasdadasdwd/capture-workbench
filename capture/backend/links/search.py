"""以请求为参考点搜索全部报文，并生成可复核的出现时间线。"""

import json
from bisect import bisect_left
from urllib.parse import urlsplit

from capture.backend.links.text import message_matches


def matched_parameters(reader, flow_id, query, digest):
    """提取匹配片段所在的完整字段，用指纹比较而不输出敏感原值。"""
    flow, fields = reader.fields(flow_id)
    items = []
    for item in fields["fields"]:
        field, value = item["field"], item["value"]
        if not field.startswith(("request.", "response.")):
            continue
        if not (query in value or query.casefold() in field.casefold()):
            continue
        if not 4 <= len(value) <= 8192:
            continue
        part = field.split(".", 1)[0]
        items.append(
            {
                "field": field,
                "fingerprint": digest(value),
                "length": len(value),
                "part": part,
                "at": flow["started"]
                + ((flow.get("duration") or 0) / 1000 if part == "response" else 0),
            }
        )
    return items


def provenance(nodes, source, digest):
    """按报文出现时间建立完整值候选链，普通片段匹配不伪装成参数传递。"""
    events = []
    uses = {}
    for node in nodes:
        seen = set()
        for item in node.get("parameters", []):
            if item["part"] == "request" and item["fingerprint"] not in seen:
                uses.setdefault(item["fingerprint"], []).append(item["at"])
                seen.add(item["fingerprint"])
            events.append(
                (item["at"], 0 if item["part"] == "response" else 1, node, item)
            )
    events.sort(key=lambda event: (event[0], event[1], event[2]["id"]))
    for times in uses.values():
        times.sort()
    response_last, request_last, edges = {}, {}, []
    linked = set()
    for at, _, node, item in events:
        fingerprint = item["fingerprint"]
        if item["part"] == "response":
            response_last[fingerprint] = (node, item)
            continue
        previous = request_last.get(fingerprint)
        response = response_last.get(fingerprint)
        candidate = (
            previous
            if previous and (not response or previous[1]["at"] > response[1]["at"])
            else response
        )
        if (
            candidate
            and candidate[0]["id"] != node["id"]
            and (node["id"], fingerprint) not in linked
        ):
            parent, origin = candidate
            from_response = origin["part"] == "response"
            edges.append(
                {
                    "id": digest(parent["id"] + node["id"] + fingerprint)[:24],
                    "from": parent["id"],
                    "to": node["id"],
                    "related_request_id": node["id"],
                    "source_field": origin["field"],
                    "target_field": item["field"],
                    "relation": "exact" if from_response else "shared_value",
                    "role": "source_candidate"
                    if from_response
                    else "same_value_sequence",
                    "reference_link": False,
                    "full_value_match": True,
                    "fingerprint": fingerprint,
                    "target_fingerprint": fingerprint,
                    "source_transform": "完整值一致",
                    "target_transform": "完整值一致",
                    "value": f"•••• · {item['length']} 字符 · {fingerprint[:10]}",
                    "target_value": f"•••• · {item['length']} 字符 · {fingerprint[:10]}",
                    "time_delta_ms": round((at - origin["at"]) * 1000, 2),
                    "available_before_target": at >= origin["at"],
                    "causality": "unconfirmed",
                }
            )
            linked.add((node["id"], fingerprint))
        request_last[fingerprint] = (node, item)
    candidates = []
    seed_values = {
        item["fingerprint"]
        for item in source.get("parameters", [])
        if item["part"] == "request"
    }
    for node in nodes:
        for item in node.get("parameters", []):
            if item["part"] != "response":
                continue
            times = uses.get(item["fingerprint"], [])
            consumers = len(times) - bisect_left(times, item["at"])
            candidates.append(
                {
                    "request_id": node["id"],
                    "url": node["url"],
                    "field": item["field"],
                    "at": item["at"],
                    "fingerprint": item["fingerprint"],
                    "matches_reference_value": item["fingerprint"] in seed_values,
                    "later_requests": consumers,
                    "earlier_than_reference": item["at"] <= source["started"],
                }
            )
    known = {
        (item["request_id"], item["field"].split("#", 1)[0]) for item in candidates
    }
    for node in nodes:
        for hit in node.get("occurrences", []):
            if (
                not hit["field"].startswith("response.")
                or (node["id"], hit["field"].split("#", 1)[0]) in known
            ):
                continue
            candidates.append(
                {
                    "request_id": node["id"],
                    "url": node["url"],
                    "field": hit["field"],
                    "at": hit["at"],
                    "fingerprint": "",
                    "matches_reference_value": False,
                    "later_requests": 0,
                    "earlier_than_reference": hit["at"] <= source["started"],
                    "partial_only": True,
                }
            )
    candidates.sort(
        key=lambda item: (
            -item["matches_reference_value"],
            -item["later_requests"],
            0 if item["field"].startswith("response.body#") else 1,
            item["at"],
        )
    )
    return edges, candidates


def search_occurrences(reader, options, cancelled, report, previous=None):
    """名称或值片段都作全文包含查询；起点不含片段也继续扫描其他请求。"""
    from capture.backend.links.worker import digest, masked, summary

    if not options.query:
        raise ValueError("选中的参数为空，请输入有效片段")
    if (previous or {}).get("_analysis_version") != 2 or (previous or {}).get(
        "_query_fingerprint"
    ) != digest(options.query):
        previous = None
    row = reader.db.execute(
        "SELECT detail,status FROM flows WHERE id=?", (options.flow_id,)
    ).fetchone()
    if row is None:
        raise FileNotFoundError("起点请求不存在")
    seed = json.loads(row[0])
    seed["status"] = row[1]
    start = seed.get("started", 0)
    # 全文搜索默认覆盖整个指定会话范围，不沿用字段追踪的五分钟窗口。
    rows = reader.search_rows(options, start)
    first_time, last_time = start, start
    scan_warnings = {}
    signatures, matches, warnings = {}, {}, []
    reused = 0
    for index, row in enumerate(rows):
        if cancelled.is_set():
            raise InterruptedError("分析已取消")
        first_time = min(first_time, row["started"])
        last_time = max(last_time, row["started"] + (row.get("duration") or 0) / 1000)
        report({"scanned": index + 1, "total": None, "matched": len(matches)})
        signature = reader.signature(row["id"])
        signatures[row["id"]] = signature
        if (
            signature
            and (previous or {}).get("_signatures", {}).get(row["id"]) == signature
        ):
            old = (previous or {}).get("_matches", {}).get(row["id"])
            if old:
                matches[row["id"]] = old
            scan_warnings[row["id"]] = (
                (previous or {}).get("_scan_warnings", {}).get(row["id"], [])
            )
            warnings.extend(scan_warnings[row["id"]])
            reused += 1
            continue
        data = reader.db.execute(
            "SELECT detail,status FROM flows WHERE id=?", (row["id"],)
        ).fetchone()
        if data is None:
            warnings.append("扫描期间有请求被删除")
            continue
        flow = json.loads(data[0])
        flow["status"] = data[1]
        hits, problems = message_matches(reader.folder, flow, options.query, cancelled)
        scan_warnings[row["id"]] = problems
        warnings.extend(problems)
        if hits:
            matches[row["id"]] = {
                "node": summary(flow),
                "hits": hits,
                "parameters": matched_parameters(
                    reader, row["id"], options.query, digest
                ),
            }
    # 每个请求的每个报文位置为一次出现；正文中的重复次数单独保留。
    occurrences = sorted(
        (
            {"request_id": id, **hit}
            for id, item in matches.items()
            for hit in item["hits"]
        ),
        key=lambda hit: (hit["at"], hit["request_id"], hit["field"]),
    )
    for number, hit in enumerate(occurrences, 1):
        hit["sequence"] = number
    source = {
        **summary(seed),
        "source": True,
        "field": "全文片段",
        "fingerprint": digest(options.query),
        "value": masked(options.query),
        "matched": options.flow_id in matches,
    }
    nodes, edges, groups = [source], [], {}
    hits_by_request = {}
    for hit in occurrences:
        hits_by_request.setdefault(hit["request_id"], []).append(hit)
    for id, item in matches.items():
        hits = hits_by_request[id]
        node = source if id == options.flow_id else {**item["node"]}
        node["occurrences"] = hits
        node["parameters"] = item["parameters"]
        node["first_sequence"] = hits[0]["sequence"]
        if id != options.flow_id:
            nodes.append(node)
            earlier = item["node"]["started"] < start
            candidate = any(
                hit["field"].startswith("response.") and hit["at"] <= start
                for hit in hits
            )
            fields = "、".join(hit["field"] for hit in hits)
            edges.append(
                {
                    "id": digest(options.flow_id + id + options.query)[:24],
                    "from": id if earlier else options.flow_id,
                    "to": options.flow_id if earlier else id,
                    "related_request_id": id,
                    "source_field": fields if earlier else "全文片段",
                    "target_field": "全文片段" if earlier else fields,
                    "relation": "contains",
                    "role": "source_candidate"
                    if earlier and candidate
                    else "earlier_use"
                    if earlier
                    else "later_use",
                    "reference_link": True,
                    "occurrences": hits,
                    "source_transform": "片段出现",
                    "target_transform": "片段出现",
                    "fingerprint": digest(options.query),
                    "value": masked(options.query),
                    "target_value": masked(options.query),
                    "time_delta_ms": round(
                        abs(item["node"]["started"] - start) * 1000, 2
                    ),
                    "available_before_target": candidate
                    if earlier
                    else source["matched"],
                    "causality": "unconfirmed",
                }
            )
        key = (node["host"], urlsplit(node["url"] or "").path)
        groups.setdefault(key, {"host": key[0], "path": key[1], "count": 0})[
            "count"
        ] += 1
    nodes.sort(key=lambda node: (node.get("started") or 0, node["id"]))
    links, source_candidates = provenance(nodes, source, digest)
    candidate_ids = {item["request_id"] for item in source_candidates}
    for node in nodes:
        node["response_match"] = node["id"] in candidate_ids
    # 参考点与每条匹配请求之间的旧连线只代表共同片段，画面以字段证据为主。
    edges = links
    return {
        "operation": "search",
        "session_id": options.session_id,
        "source": source,
        "nodes": nodes,
        "edges": edges,
        "groups": list(groups.values()),
        "source_candidates": source_candidates,
        "scope": {
            "scanned": len(signatures),
            "parsed": len(signatures) - reused,
            "reused": reused,
            "matched_requests": len(matches),
            "displayed_requests": len(matches),
            "occurrence_count": len(occurrences),
            "scan_limit_reached": False,
            "node_limit_reached": False,
            "direction": options.direction,
            "start": first_time,
            "end": last_time,
            "source_candidates": sum(
                edge["role"] == "source_candidate" for edge in edges
            ),
            "earlier_uses": sum(edge["role"] == "earlier_use" for edge in edges),
            "later_uses": sum(edge["role"] == "later_use" for edge in edges),
            "scanned_request_ids": list(signatures),
            "text_search_complete": not warnings,
        },
        "warnings": list(dict.fromkeys(warnings))[:30]
        + [
            "首次出现指当前分析范围内首次捕获；片段或名称相同仅代表候选关联，不能证明参数生成来源。"
        ],
        "_signatures": signatures,
        "_matches": matches,
        "_scan_warnings": scan_warnings,
        "_query_fingerprint": digest(options.query),
        "_analysis_version": 2,
    }
