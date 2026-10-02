"""启动入口的浏览器容错与服务就绪时序。"""

import asyncio
from unittest.mock import Mock

import pytest
import uvicorn

import main


@pytest.mark.parametrize("result", [False, RuntimeError("no browser")])
def test_browser_failure_keeps_service_usable(monkeypatch, caplog, result):
    """系统缺少浏览器或浏览器抛异常时仍返回，提示实际访问地址。"""
    browser = (
        Mock(side_effect=result)
        if isinstance(result, Exception)
        else Mock(return_value=result)
    )
    monkeypatch.setattr(main.webbrowser, "open", browser)
    main.open_console("http://localhost:9876/")
    assert "http://localhost:9876/" in caplog.text


@pytest.mark.parametrize(
    "started,enabled", [(True, True), (False, True), (True, False)]
)
def test_browser_only_opens_after_successful_startup(monkeypatch, started, enabled):
    """监听失败或关闭自动打开时不启动浏览器线程。"""
    server = main.ConsoleServer(
        uvicorn.Config("capture.backend.app:app", port=9876), enabled
    )

    async def startup(self, sockets=None):
        self.started = started

    monkeypatch.setattr(uvicorn.Server, "startup", startup)
    thread = Mock()
    monkeypatch.setattr(main.threading, "Thread", thread)
    asyncio.run(server.startup())
    if started and enabled:
        assert thread.call_args.kwargs["args"] == ("http://127.0.0.1:9876/",)
        thread.return_value.start.assert_called_once()
    else:
        thread.assert_not_called()
