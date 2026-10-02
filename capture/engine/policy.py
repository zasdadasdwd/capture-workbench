"""抓包和重放共用域名规则，不支持含糊的子串匹配。"""

import ipaddress
import re


def normalize_host(host: str) -> str:
    """统一大小写、尾部点和 IDN 编码，避免同一域名出现多种写法。"""
    return host.strip().rstrip(".").lower().encode("idna").decode("ascii")


def normalize_pattern(value: str) -> str:
    """校验精确域名、IP 或 *.子域名规则，拒绝 URL 和带端口的输入。"""
    value = value.strip()
    wildcard = value.startswith("*.")
    host = normalize_host(value[2:] if wildcard else value)
    if not host:
        raise ValueError("域名不能为空")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part)
            for part in host.split(".")
        ):
            raise ValueError(f"域名格式不正确：{value}，请勿包含协议、路径或端口")
    else:
        if wildcard:
            raise ValueError("IP 地址不支持通配符")
    return "*." + host if wildcard else host


def matches(host: str, patterns: list[str]) -> bool:
    """精确或按子域名边界匹配；*.example.com 不匹配 example.com。"""
    host = normalize_host(host)
    return any(
        host.endswith(pattern[1:]) if pattern.startswith("*.") else host == pattern
        for pattern in patterns
    )


def is_blocked(host: str, settings: dict) -> bool:
    """拒绝开关开启且列表命中时返回 True。"""
    return settings["blocking_enabled"] and matches(host, settings["blocked_domains"])


def should_decrypt(host: str, settings: dict) -> bool:
    """根据 all/list/passthrough 判定 TLS 是否进入解密。"""
    return settings["tls_mode"] == "all" or (
        settings["tls_mode"] == "list" and matches(host, settings["tls_domains"])
    )
