"""设置发布失败时恢复磁盘配置和常驻代理的集成测试。"""

import socket

import httpx

import config
from capture.support.tests.test_workbench import free_port

pytest_plugins = ("capture.support.tests.test_workbench",)


def test_occupied_port_restores_settings_and_forwarding(client, origin):
    """新端口被占用时，旧版本配置和真实代理仍可转发请求。"""
    api, data = client
    previous = api.get("/api/status").json()["settings"]
    old_port = previous["listen_port"]
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", 0))
        occupied.listen()
        new_port = occupied.getsockname()[1]
        response = api.put("/api/settings", json={**previous, "listen_port": new_port})
        assert response.status_code == 409, response.text
        assert "已恢复原配置和代理" in response.json()["detail"]

    current = api.get("/api/status").json()
    assert current["running"] and not current["recording"]
    assert current["settings"] == previous
    assert current["policy_version"] == previous["version"]
    assert config.load_settings().model_dump() == previous
    assert (data / "settings.json").exists()
    with httpx.Client(proxy=f"http://127.0.0.1:{old_port}", trust_env=False) as proxy:
        forwarded = proxy.get(origin["http"] + "/after-rollback")
    assert forwarded.status_code == 200
    assert forwarded.json()["path"] == "/after-rollback"


def test_failed_restore_reports_error_and_notifies(client, monkeypatch):
    """两次重启都失败时保留旧设置，并报告旧代理恢复失败。"""
    api, _ = client
    state = api.app.state.workbench
    previous = state.settings.model_dump()
    attempted_ports = []
    events = []
    original_notify = state.notify

    async def fail_reconfigure(settings):
        attempted_ports.append(settings.listen_port)
        raise RuntimeError(f"模拟端口 {settings.listen_port} 启动失败")

    def record_notification(event):
        events.append(event)
        original_notify(event)

    monkeypatch.setattr(state.engine, "reconfigure", fail_reconfigure)
    monkeypatch.setattr(state, "notify", record_notification)
    new_port = free_port()
    response = api.put("/api/settings", json={**previous, "listen_port": new_port})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert f"配置更新失败：模拟端口 {new_port} 启动失败" in detail
    assert "恢复失败：旧代理恢复失败" in detail
    assert attempted_ports == [new_port, previous["listen_port"]]
    assert state.settings.model_dump() == previous
    assert config.load_settings().model_dump() == previous
    assert {"type": "status"} in events
