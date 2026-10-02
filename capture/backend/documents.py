"""将可分享的 Markdown 文档渲染为带复制功能的阅读页面。"""

from functools import lru_cache

import markdown

from config import ROOT


@lru_cache(maxsize=2)
def markdown_content(source):
    """缓存相同文档内容，代码块保留缩进并按文本转义。"""
    return markdown.markdown(source, extensions=["fenced_code"])


def certificate_page():
    """以 Markdown 为唯一正文来源，编辑安装说明后页面自动更新。"""
    source = (ROOT / "capture/support/docs/证书安装.md").read_text(encoding="utf-8")
    template = (ROOT / "capture/web/certificate.html").read_text(encoding="utf-8")
    return template.replace("{{content}}", markdown_content(source))
