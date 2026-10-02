"""用户 Hook 模板：直接改这个类，保存后重启服务并在设置中启用。"""

from capture.plugins.base import BaseHook


class TemplateHook(BaseHook):
    """默认不修改任何请求或响应。"""

    name = "template"
    description = "根目录模板：默认不修改报文"

    def on_request(self, flow, context) -> None:
        """发送前可修改 flow.request，如 query、headers、content。"""
        # 示例：flow.request.headers["X-Test"] = "1"

    def on_response(self, flow, context) -> None:
        """返回后可修改 flow.response，如 status_code、headers、content。"""
        # 示例：flow.response.headers["X-Test"] = "1"
