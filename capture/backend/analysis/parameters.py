"""提取可定位的 HTTP 参数，保留重复字段和 JSON Pointer 路径。"""

import base64
import json
from collections import defaultdict
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qsl, unquote, urlsplit


def extract_parameters(flow: dict, max_fields=1000) -> dict:
    """有界提取 query、头部、Cookie、JSON 和表单；截断正文不作完整解析。"""
    fields = []
    warnings = []

    def add(path, value):
        if len(fields) >= max_fields:
            return
        text = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
        if len(text) > 8192:
            warnings.append(f"字段过长，已跳过：{path}")
            return
        fields.append({"field": path, "value": text})

    def pairs(prefix, items, lower=False):
        counts = defaultdict(int)
        for name, value in items:
            name = name.lower() if lower else name
            add(f"{prefix}.{name}[{counts[name]}]", value)
            counts[name] += 1

    for part in ("original_request", "request", "response"):
        message = flow.get(part)
        if not message:
            continue
        if part != "response":
            pairs(
                f"{part}.query",
                parse_qsl(
                    urlsplit(message.get("url", flow.get("url", ""))).query,
                    keep_blank_values=True,
                ),
            )
        headers = message.get("headers", [])
        pairs(f"{part}.headers", headers, lower=True)
        content_type = ""
        cookie_counts = defaultdict(int)
        for name, value in headers:
            name = name.lower()
            if name == "content-type":
                content_type = value.lower()
            if name in ("cookie", "set-cookie"):
                cookie = SimpleCookie()
                try:
                    cookie.load(value)
                    for key, morsel in cookie.items():
                        index = cookie_counts[key]
                        add(f"{part}.cookies.{key}[{index}]", morsel.value)
                        cookie_counts[key] += 1
                except CookieError:
                    pass
        if any(
            message.get(key)
            for key in ("truncated", "display_truncated", "decode_error")
        ):
            warnings.append(f"{part} 正文不完整或无法解码，未解析正文参数")
            continue
        body = message.get("body_text", "")
        try:
            parsed = json.loads(body)
        except (ValueError, TypeError):
            if "application/x-www-form-urlencoded" in content_type:
                pairs(f"{part}.form", parse_qsl(body, keep_blank_values=True))
            continue
        pending = [("", parsed, 0)]
        while pending and len(fields) < max_fields:
            path, value, depth = pending.pop()
            if depth > 16:
                warnings.append(f"{part} JSON 嵌套超过 16 层，部分字段未解析")
                continue
            if isinstance(value, (dict, list)):
                entries = value.items() if isinstance(value, dict) else enumerate(value)
                # 不把大数组的全部元素提前放入栈。
                for key, child in entries:
                    if len(pending) >= max_fields:
                        warnings.append(f"{part} JSON 字段数量超过上限")
                        break
                    escaped = str(key).replace("~", "~0").replace("/", "~1")
                    pending.append((f"{path}/{escaped}", child, depth + 1))
            else:
                add(f"{part}.body#{path}", value)
    if len(fields) >= max_fields:
        warnings.append("参数数量达到上限，结果可能不完整")
    return {"fields": fields, "warnings": list(dict.fromkeys(warnings))}


def select_parameter(fields: list, selector: str) -> dict:
    """无索引的简写仅在唯一匹配时接受，重复字段要求明确索引。"""
    if not selector.startswith(("request.", "original_request.", "response.")):
        selector = "request." + selector
    matches = [
        item
        for item in fields
        if item["field"] == selector or item["field"].rsplit("[", 1)[0] == selector
    ]
    if len(matches) != 1:
        raise ValueError(
            "参数不存在或存在多个值，请从 parameters 中选择完整 field 路径"
        )
    return matches[0]


def value_variants(value: str) -> dict:
    """只尝试可解释的轻量转换，不把解码成功当作加密算法还原。"""
    variants = {value: "原值"}
    if value.startswith("Bearer "):
        variants[value[7:]] = "去除 Bearer 前缀"
    decoded = unquote(value)
    if decoded != value:
        variants[decoded] = "URL 解码"
    if 8 <= len(value) <= 8192:
        try:
            raw = base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )
            text = raw.decode("utf-8")
            if text and text.isprintable():
                variants[text] = "Base64 解码"
        except (ValueError, UnicodeError):
            pass
    return variants
