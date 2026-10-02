"""在这里定义请求 Hook；继承 BaseHook 后会自动出现在设置列表。"""

from .base import BaseHook


class RequestHook(BaseHook):
    """默认参数处理入口。"""

    name = "request_hook"
    description = "默认请求参数处理入口"

    def on_request(self, flow, context) -> None:
        """按需改写请求；context 提供 session_id、config、logger 和 now_ms。"""
        # 示例：
        # if flow.request.host == "api.example.com":
        #     flow.request.query["page"] = "2"
        #     flow.request.headers["X-Timestamp"] = str(context.now_ms())


class ExampleHeaders(BaseHook):
    """示例：只修改 example.com 的 Header。"""

    name = "example_headers"
    description = "示例：给 example.com 添加测试 Header"

    def on_request(self, flow, context) -> None:
        if flow.request.host == "example.com":
            flow.request.headers["X-Capture-Test"] = "1"


class ExampleQuery(BaseHook):
    """示例：修改 api.example.com 的分页参数。"""

    name = "example_query"
    description = "示例：修改 api.example.com 的 page 参数"

    def on_request(self, flow, context) -> None:
        if flow.request.host == "api.example.com":
            flow.request.query["page"] = "2"
