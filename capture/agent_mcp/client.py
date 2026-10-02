"""MCP 通过本机管理 API 访问数据，复用工作台的校验与任务管理。"""

from urllib.parse import quote, urlsplit

import httpx


class WorkbenchClient:
    """持有可复用的异步 HTTP 连接，禁止代理环境变量和跨站重定向。"""

    def __init__(self, base_url, transport=None):
        url = urlsplit(base_url)
        if (
            url.scheme != "http"
            or url.hostname not in ("localhost", "127.0.0.1")
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("工作台地址必须是本机 http://127.0.0.1:端口")
        self.http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            trust_env=False,
            follow_redirects=False,
            timeout=30,
            transport=transport,
        )

    @staticmethod
    def path(*parts):
        """逐段编码会话与请求 ID，禁止把标识拼接成额外的 API 路径。"""
        for part in parts:
            if not part or part in (".", "..") or "/" in part or "\\" in part:
                raise ValueError("无效的会话或请求标识")
        return "/".join(quote(part, safe="") for part in parts)

    async def request(self, method, path, **kwargs):
        response = await self.http.request(method, path, **kwargs)
        if response.is_error:
            try:
                message = response.json().get("detail", "API 请求失败")
            except ValueError:
                message = "API 请求失败"
            raise ValueError(f"工作台 API {response.status_code}: {message}")
        return response.json()

    async def flow(self, session_id, flow_id, preview=True):
        return await self.request(
            "GET",
            "/api/sessions/" + self.path(session_id, "flows", flow_id),
            params={"preview": preview},
        )

    async def close(self):
        await self.http.aclose()
