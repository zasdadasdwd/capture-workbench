"""目录分段匹配、外部代理校验与动态地址展示的回归验证。"""

import pytest
from pydantic import ValidationError

from capture.backend import network
from capture.backend.filters import FlowFilters
from capture.backend.storage import Store
from config import Settings


def test_directory_boundaries_and_sources(tmp_path):
    """目录包含子目录和查询参数，但不能误匹配相似名称或 Query 中的斜线。"""
    store = Store(tmp_path / "captures")
    session = store.create_session(Settings().model_dump())
    urls = [
        "http://www.host.com/am1",
        "https://www.host.com/am1/am2?a=1",
        "https://www.host.com/am1/am2?a=2",
        "http://www.host.com/am10",
        "http://www.host.com?next=/am1",
        "http://other.test/am1",
    ]
    for index, url in enumerate(urls):
        store.save_flow(
            session,
            {
                "id": str(index),
                "url": url,
                "host": "other.test" if index == 5 else "www.host.com",
                "status": "complete",
                "source": "replay" if index == 2 else "capture",
            },
        )
    rows = store.list_flows(
        session, filters=FlowFilters(host="www.host.com", path_prefix="/am1")
    )
    assert {row["id"] for row in rows["items"]} == {"0", "1", "2"}
    assert {
        row["id"]
        for row in store.list_flows(
            session,
            filters=FlowFilters(
                host="www.host.com", path_prefix="/am1/am2", source="replay"
            ),
        )["items"]
    } == {"2"}
    assert {
        "host": "www.host.com",
        "path": "/am1/am2",
        "count": 2,
    } in store.directories(session)
    store.close()


@pytest.mark.parametrize(
    "address",
    [
        "socks5://localhost:7890",
        "http://localhost",
        "http://localhost:8080",
        "http://localhost:7890/path",
        "",
    ],
)
def test_invalid_upstream_addresses(address):
    """拒绝不支持的代理协议、缺失端口以及代理指向自身。"""
    with pytest.raises(ValidationError):
        Settings(connection_mode="upstream", upstream_proxy=address)


def test_network_address_tracks_port_and_interface(monkeypatch):
    """动态读取 IP 和端口，区分仅本机与局域网监听，不展示固定旧地址。"""
    monkeypatch.setattr(network, "lan_ip", lambda: "192.168.1.20")
    assert network.proxy_addresses(Settings()) == {
        "lan_ip": "192.168.1.20",
        "lan_address": "192.168.1.20:8080",
        "listen_address": "127.0.0.1:8080",
        "lan_accessible": False,
    }
    assert (
        network.proxy_addresses(Settings(listen_host="0.0.0.0", listen_port=9090))[
            "lan_address"
        ]
        == "192.168.1.20:9090"
    )
    monkeypatch.setattr(network, "lan_ip", lambda: None)
    assert network.proxy_addresses(Settings())["lan_address"] is None
